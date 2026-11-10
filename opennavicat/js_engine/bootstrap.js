(() => {
  'use strict';
  const host = globalThis.__native;
  delete globalThis.__native;
  const stringify=JSON.stringify, parse=JSON.parse, keys=Object.keys, descriptors=Object.getOwnPropertyDescriptors;
  const ownKeys=Reflect.ownKeys, getProto=Object.getPrototypeOf, define=Object.defineProperty;
  const freeze=Object.freeze, create=Object.create, big=BigInt, isSafe=Number.isSafeInteger;
  const brands=new WeakMap(), epoch=Date.prototype.getTime, dateISO=Date.prototype.toISOString;
  const NativeDate=Date, NativeRegExp=RegExp, NativeArray=Array, NativeU8=Uint8Array;
  const ObjectProto=Object.prototype, ArrayProto=Array.prototype, NativeObject=Object, NativeNumber=Number;
  const NativeWeakMap=WeakMap, NativeWeakSet=WeakSet, NativeJSON=JSON, arrayMap=ArrayProto.map;
  const NativeString=String,objectIs=Object.is,bigIntToString=BigInt.prototype.toString,functionCall=Function.prototype.call;
  const arrayPush=ArrayProto.push,arraySlice=ArrayProto.slice,arraySome=ArrayProto.some,arrayIncludes=ArrayProto.includes;
  const weakGet=WeakMap.prototype.get, weakSet=WeakMap.prototype.set, weakHas=WeakSet.prototype.has, weakAdd=WeakSet.prototype.add, weakDelete=WeakSet.prototype.delete;
  function guard() {
    if(Object!==NativeObject||String!==NativeString||Object.is!==objectIs||BigInt.prototype.toString!==bigIntToString||Function.prototype.call!==functionCall||
      Number!==NativeNumber||WeakMap!==NativeWeakMap||WeakSet!==NativeWeakSet||JSON!==NativeJSON||JSON.stringify!==stringify||JSON.parse!==parse||
      ArrayProto.map!==arrayMap||ObjectProto.toJSON!==undefined||ArrayProto.toJSON!==undefined||WeakMap.prototype.get!==weakGet||WeakMap.prototype.set!==weakSet||
      ArrayProto.push!==arrayPush||ArrayProto.slice!==arraySlice||ArrayProto.some!==arraySome||ArrayProto.includes!==arrayIncludes||
      WeakSet.prototype.has!==weakHas||WeakSet.prototype.add!==weakAdd||WeakSet.prototype.delete!==weakDelete)
      throw new TypeError('Mutated bridge intrinsics are not supported by this prototype');
  }
  const min64=-(1n<<63n), max64=(1n<<63n)-1n;
  const i64 = value => { if(typeof value==='number'&&!isSafe(value))throw new RangeError('Use a string or BigInt for exact Int64');const n=big(value);if(n<min64||n>max64)throw new RangeError('BSON Int64 range');return n; };
  function tagged(kind,data,text,number) {
    const obj=create(null);brands.set(obj,[kind,data]);
    define(obj,'toString',{value:()=>text(),enumerable:false});
    define(obj,'valueOf',{value:()=>number?number():(()=>{throw new TypeError('Explicit BSON conversion required')})(),enumerable:false});
    return freeze(obj);
  }
  function Long(value) {const n=i64(value);return tagged('int64',n,()=>`Long("${n.toString()}")`,()=>n);}
  function Int32(value) {if(typeof value!=='number'||!isSafe(value)||value<-(2**31)||value>=2**31)throw new RangeError('Int32 range');return tagged('int32',value,()=>`Int32("${String(value)}")`,()=>value);}
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
    if(value===undefined&&allowLargeBigInt)return ['js-undefined'];
    if(value===null)return ['null'];
    if(typeof value==='boolean')return ['bool',value];
    if(typeof value==='string')return ['string',value];
    if(typeof value==='bigint'){if(allowLargeBigInt)return ['js-bigint',value.toString()];return ['int64',i64(value).toString()];}
    if(typeof value==='number'){
      // Actual Navicat18 writes ordinary JS Number as BSON Double, including
      // integers and unsafe literals already rounded by JavaScript itself.
      // Explicit Int32/Long brands remain separate and exact.
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
        const props=descriptors(value),items=[];
        for(let i=0;i<value.length;i++){const d=props[String(i)];if(!d||!d.enumerable||!('value'in d))throw new TypeError('Array accessors are not BSON data');items.push(encode(d.value,depth+1,seen,allowLargeBigInt));}
        return ['array',items];
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
    guard();
    const reply=parse(host(stringify({kind:'command',tree:encode(value)})));
    if(reply.ok!==true)throw new Error(reply.error||'Parent rejected command');
    return decode(reply.tree);
  }
  function cursor(cmd) {
    let documents=null,index=0;
    function set(key,value){if(documents!==null)throw new Error('Cursor already executed');cmd[key]=value;return api;}
    function load(){if(documents===null)documents=command(cmd).cursor.firstBatch;}
    const api={sort(value){return set('sort',value);},skip(value){return set('skip',value);},
      limit(value){return set('limit',value);},batchSize(value){if(cmd.aggregate){if(documents!==null)throw new Error('Cursor already executed');cmd.cursor.batchSize=value;return api;}return set('batchSize',value);},
      maxTimeMS(value){return set('maxTimeMS',value);},
      hasNext(){load();return index<documents.length;},next(){load();if(index>=documents.length)throw new Error('Cursor exhausted');return documents[index++];},
      toArray(){load();const result=documents.slice(index);index=documents.length;return result;},
      forEach(fn){if(typeof fn!=='function')throw new TypeError('Cursor callback');while(api.hasNext())fn(api.next());},
      map(fn){if(typeof fn!=='function')throw new TypeError('Cursor callback');const result=[];while(api.hasNext())result.push(fn(api.next()));return result;},
      close(){documents=[];index=0;}};
    return api;
  }
  function options(value,allowed){if(!value||typeof value!=='object')throw new TypeError('Options document required');for(const key of keys(value))if(!allowed.includes(key))throw new TypeError('Unsupported prototype option: '+key);return value;}
  function collection(name) {if(typeof name!=='string'||!name||name.includes('\0'))throw new TypeError('Collection name');
    function update(filter,value,opts,multi){const settings=options(opts,['upsert','collation','arrayFilters','hint']);const reply=command({update:name,updates:[{q:filter,u:value,multi,...settings}],ordered:true});return {acknowledged:true,matchedCount:reply.n,modifiedCount:reply.nModified,upserted:reply.upserted||[]};}
    function remove(filter,opts,limit){const settings=options(opts,['collation','hint']);const reply=command({delete:name,deletes:[{q:filter,limit,...settings}],ordered:true});return {acknowledged:true,deletedCount:reply.n};}
    return freeze({
      find(filter={},projection){const cmd={find:name,filter};if(projection!==undefined)cmd.projection=projection;return cursor(cmd);},
      findOne(filter={},projection){const cmd={find:name,filter,limit:1,singleBatch:true};if(projection!==undefined)cmd.projection=projection;const docs=command(cmd).cursor.firstBatch;return docs.length?docs[0]:null;},
      aggregate(pipeline,opts={}){const settings=options(opts,['allowDiskUse','maxTimeMS','collation','hint']);return cursor({aggregate:name,pipeline,cursor:{},...settings});},
      insertOne(document){if(!document||!Object.prototype.hasOwnProperty.call(document,'_id'))throw new TypeError('Prototype insertOne requires an explicit _id');command({insert:name,documents:[document],ordered:true});return {acknowledged:true,insertedId:document._id};},
      insertMany(documents,opts={}){options(opts,['ordered']);for(const doc of documents)if(!doc||!Object.prototype.hasOwnProperty.call(doc,'_id'))throw new TypeError('Prototype insertMany requires explicit _id values');command({insert:name,documents,ordered:opts.ordered!==false});return {acknowledged:true,insertedIds:documents.map(doc=>doc._id)};},
      updateOne(filter,value,opts={}){return update(filter,value,opts,false);},updateMany(filter,value,opts={}){return update(filter,value,opts,true);},
      deleteOne(filter,opts={}){return remove(filter,opts,1);},deleteMany(filter,opts={}){return remove(filter,opts,0);},
      countDocuments(filter={}){return command({count:name,query:filter}).n;},getName(){return name;}
    });
  }
  const db=new Proxy(freeze({getCollection:collection,runCommand:command}),{get(target,name){if(typeof name!=='string')return undefined;return Object.prototype.hasOwnProperty.call(target,name)?target[name]:collection(name);}});
  for(const [name,value]of Object.entries({db,Long,NumberLong:Long,Int32,NumberInt:Int32,Decimal128,NumberDecimal:Decimal128,ObjectId,Binary,BinData,BSONRegex,Timestamp,MinKey,MaxKey,Code,DBRef}))define(globalThis,name,{value,writable:false,configurable:false});
  return value => {guard();return stringify(encode(value,0,new WeakSet(),true));};
})()
