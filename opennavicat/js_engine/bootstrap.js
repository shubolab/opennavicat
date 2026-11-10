(() => {
  'use strict';
  const host = globalThis.__native;
  delete globalThis.__native;
  const stringify=JSON.stringify, parse=JSON.parse, keys=Object.keys, descriptors=Object.getOwnPropertyDescriptors;
  const ownKeys=Reflect.ownKeys, getProto=Object.getPrototypeOf, define=Object.defineProperty;
  const freeze=Object.freeze, create=Object.create, big=BigInt, isSafe=Number.isSafeInteger;
  const brands=new WeakMap(), epoch=Date.prototype.getTime, dateISO=Date.prototype.toISOString;
  const NativeDate=Date, NativeRegExp=RegExp, NativeArray=Array, NativeU8=Uint8Array;
  const min64=-(1n<<63n), max64=(1n<<63n)-1n;
  const i64 = value => { if(typeof value==='number'&&!isSafe(value))throw new RangeError('Use a string or BigInt for exact Int64');const n=big(value);if(n<min64||n>max64)throw new RangeError('BSON Int64 range');return n; };
  function tagged(kind,data,text,number) {
    const obj=create(null);brands.set(obj,[kind,data]);
    define(obj,'toString',{value:()=>text(),enumerable:false});
    define(obj,'valueOf',{value:()=>number?number():(()=>{throw new TypeError('Explicit BSON conversion required')})(),enumerable:false});
    return freeze(obj);
  }
  function Long(value) {const n=i64(value);return tagged('int64',n,()=>n.toString(),()=>n);}
  function Int32(value) {if(typeof value!=='number'||!isSafe(value)||value<-(2**31)||value>=2**31)throw new RangeError('Int32 range');return tagged('int32',value,()=>String(value),()=>value);}
  function Double(value) {const n=Number(value);return tagged('double',n,()=>String(n),()=>n);}
  function Decimal128(value) {if(typeof value!=='string')throw new TypeError('Decimal128 requires exact string');return tagged('decimal128',value,()=>value);}
  function ObjectId(value) {if(typeof value!=='string'||!/^[a-fA-F0-9]{24}$/.test(value))throw new TypeError('ObjectId requires 24 hex characters');const h=value.toLowerCase();return tagged('objectid',h,()=>h);}
  function DateMS(value) {const n=i64(value);return tagged('date',n,()=>n.toString());}
  const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  function base64(bytes) {let s='';for(let i=0;i<bytes.length;i+=3){const n=(bytes[i]<<16)|((bytes[i+1]||0)<<8)|(bytes[i+2]||0);s+=alphabet[(n>>>18)&63]+alphabet[(n>>>12)&63]+(i+1<bytes.length?alphabet[(n>>>6)&63]:'=')+(i+2<bytes.length?alphabet[n&63]:'=');}return s;}
  function Binary(bytes,subtype=0) {
    if(!isSafe(subtype)||subtype<0||subtype>255)throw new RangeError('Binary subtype');
    let value;
    if(bytes instanceof NativeU8)value=base64(bytes);
    else if(typeof bytes==='string'&&/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(bytes))value=bytes;
    else throw new TypeError('Binary requires Uint8Array or base64');
    if(value.length>349528)throw new RangeError('Binary input limit');
    return tagged('binary',[value,subtype],()=>value);
  }
  function BinData(subtype,text){return Binary(text,subtype);}
  function BSONRegex(pattern,options='') {if(typeof pattern!=='string'||typeof options!=='string'||/[^ilmsux]/.test(options))throw new TypeError('BSON regex');const flags=[...new Set(options)].sort().join('');return tagged('regex',[pattern,flags],()=>pattern);}
  function Timestamp(t,i) {if(!isSafe(t)||!isSafe(i)||t<0||i<0||t>0xffffffff||i>0xffffffff)throw new RangeError('Timestamp range');return tagged('timestamp',[t,i],()=>`${t}:${i}`);}
  function MinKey(){return tagged('minkey',null,()=>'MinKey');}
  function MaxKey(){return tagged('maxkey',null,()=>'MaxKey');}
  function Code(text,scope=null){if(typeof text!=='string'||(scope!==null&&typeof scope!=='object'))throw new TypeError('BSON code data');return tagged('code',[text,scope],()=>text);}
  function DBRef(collection,id,database=null){if(typeof collection!=='string'||(database!==null&&typeof database!=='string'))throw new TypeError('DBRef');return tagged('dbref',[collection,id,database],()=>collection);}
  function doubleText(n) {return Object.is(n,-0)?'-0':Number.isNaN(n)?'NaN':n===Infinity?'Infinity':n===-Infinity?'-Infinity':String(n);}
  function encode(value,depth=0,seen=new WeakSet(),allowLargeBigInt=false) {
    if(depth>80)throw new RangeError('BSON bridge depth');
    if(value===null)return ['null'];
    if(typeof value==='boolean')return ['bool',value];
    if(typeof value==='string')return ['string',value];
    if(typeof value==='bigint'){if(allowLargeBigInt)return ['js-bigint',value.toString()];return ['int64',i64(value).toString()];}
    if(typeof value==='number'){
      if(isSafe(value)&&value>=-(2**31)&&value<2**31&&!Object.is(value,-0))return ['int32',value];
      if(Number.isInteger(value)&&!isSafe(value))throw new RangeError('Unsafe integral Number: use explicit Long/BigInt or Double');
      return ['double',doubleText(value)];
    }
    if(typeof value!=='object')throw new TypeError('Unsupported BSON value');
    if(seen.has(value))throw new TypeError('Cyclic BSON data');
    seen.add(value);
    try {
      const b=brands.get(value);
      if(b) {
        const [tag,data]=b;
        if(tag==='int64'||tag==='date')return [tag,data.toString()];
        if(tag==='double')return [tag,doubleText(data)];
        if(tag==='binary'||tag==='regex'||tag==='timestamp')return [tag,...data];
        if(tag==='minkey'||tag==='maxkey')return [tag];
        if(tag==='code')return [tag,data[0],data[1]===null?null:encode(data[1],depth+1,seen)];
        if(tag==='dbref')return [tag,data[0],encode(data[1],depth+1,seen),data[2]];
        return [tag,data];
      }
      if(value instanceof NativeDate){const n=epoch.call(value);if(!isSafe(n))throw new TypeError('Invalid BSON Date');return ['date',big(n).toString()];}
      if(value instanceof NativeU8)return ['binary',base64(value),0];
      if(value instanceof NativeRegExp)return encode(BSONRegex(value.source,value.flags),depth+1,seen);
      if(NativeArray.isArray(value)) {
        const names=keys(value);if(names.length!==value.length||names.some((k,i)=>k!==String(i)))throw new TypeError('Sparse or decorated arrays are not accepted');
        return ['array',value.map(x=>encode(x,depth+1,seen,allowLargeBigInt))];
      }
      if(getProto(value)!==Object.prototype&&getProto(value)!==null)throw new TypeError('Unknown object prototype');
      const fields=descriptors(value), result=[];
      for(const key of ownKeys(fields)) {
        const d=fields[key];if(typeof key!=='string'||!d.enumerable||!('value'in d))throw new TypeError('BSON data must have enumerable string data properties');
        result.push([key,encode(d.value,depth+1,seen,allowLargeBigInt)]);
      }
      return ['document',result];
    } finally {seen.delete(value);}
  }
  function decode(tree,depth=0) {
    if(depth>80||!NativeArray.isArray(tree))throw new TypeError('Invalid host data');
    const [kind,a,b,c]=tree;
    switch(kind) {
      case 'null':return null;case 'bool':case 'string':return a;
      case 'int32':return Int32(a);case 'int64':return Long(a);case 'double':return Double(a);
      case 'decimal128':return Decimal128(a);case 'objectid':return ObjectId(a);case 'date':return DateMS(a);
      case 'binary':return Binary(a,b);case 'regex':return BSONRegex(a,b);case 'timestamp':return Timestamp(a,b);
      case 'minkey':return MinKey();case 'maxkey':return MaxKey();case 'code':return Code(a,b===null?null:decode(b,depth+1));case 'dbref':return DBRef(a,decode(b,depth+1),c);
      case 'array':return a.map(x=>decode(x,depth+1));
      case 'document':{const obj={};for(const [key,value]of a)define(obj,key,{value:decode(value,depth+1),enumerable:true,writable:true,configurable:true});return obj;}
      default:throw new TypeError('Unknown host data type');
    }
  }
  function command(value) {
    const reply=parse(host(stringify({kind:'command',tree:encode(value)})));
    if(reply.ok!==true)throw new Error(reply.error||'Parent rejected command');
    return decode(reply.tree);
  }
  function collection(name) {if(typeof name!=='string'||!name||name.includes('\0'))throw new TypeError('Collection name');return freeze({
    find(filter={}) {const cmd={find:name,filter};return {sort(value){cmd.sort=value;return this;},limit(value){cmd.limit=value;return this;},toArray(){return command(cmd).documents;}};},
    aggregate(pipeline){return {toArray(){return command({aggregate:name,pipeline,cursor:{}}).documents;}};}
  });}
  const db=freeze({getCollection:collection,runCommand:command});
  for(const [name,value]of Object.entries({db,Long,NumberLong:Long,Int32,NumberInt:Int32,Double,Decimal128,NumberDecimal:Decimal128,ObjectId,DateMS,Binary,BinData,BSONRegex,Timestamp,MinKey,MaxKey,Code,DBRef}))define(globalThis,name,{value,writable:false,configurable:false});
  return value => stringify(encode(value,0,new WeakSet(),true));
})()
