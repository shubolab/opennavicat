"""Navicat 18.0.5 aggregation-stage editor templates observed in the reference."""
from __future__ import annotations


CREATE_TOOL_STAGES = frozenset({"$match", "$project", "$sort", "$limit", "$skip"})


STAGE_EDITOR_TEMPLATES = {
    "$addFields": '''{
/* Add new fields to documents.      *
 * field: name of field to be added. *
 * value: value of the new field.    *
 { <field1> : <value1>,
   <field2> : <value2>,
   ...
 }
 */
}''',
    "$bucket": '''{
/* Categorize documents into groups(buckets) based *
 * on a specified expression and boundaries.       *
 * groupBy: expression to group documents by.      *
 * boundaries: lowerbound values for each bucket.  *
 * default: (Optional) _id of an additional bucket *
 *   that contains all documents that do not fall  *
 *   into a bucket specified by boundaries.        *
 * output: (Optional) specify additional fields to *
 *   be included in output documents. Must use     *
 *   accumulator expressions.                      *
 { groupBy : <expression>,
   boundaries : [<boundary1>, <boundary2>, ...],
   (Optional) default : <default_bucket_id>,
   (Optional) output :
   { <field1> : {<accumulator1> : <expression1>},
     <field2> : {<accumulator2> : <expression2>},
     ...
   }
 }
 */
}''',
    "$bucketAuto": '''{
/* Categorize documents into specific number of     *
 * groups(buckets) based on a specified expression. *
 * groupBy: expression to group documents by.       *
 * buckets: number of groups to group the documents *
 *   into.                                          *
 * output: (Optional) specify additional fields to  *
 *   be included in output documents. Must use      *
 *   accumulator expressions.                       *
 * granularity: (Optional) the preferred number     *
 *   series to use.                                 *
 { groupBy: <expression>,
   buckets: <number_of_buckets>,
   (Optional) output :
   { <field1> : {<accumulator1> : <expression1>},
     <field2> : {<accumulator2> : <expression2>},
     ...
   },
   (Optional) granularity : <string>
 }
 */
}''',
    "$changeStream": '''{
/* Return a Change Stream cursor on a collection,    *
 * a database, or an entire cluster. Must be used as *
 * the first stage.                                  *
 * fullDocumentBeforeChange: Whether to include the  *
 *   full document from before the change.           *
 * resumeAfter: Specifies a resume token as the      *
 *   logical starting point for the change stream.   *
 * showExpandedEvents: Whether to include additional *
 *   change events.                                  *
 * startAfter: Specifies a resume token as the       *
 *   logical starting point for the change stream.   *
 * startAtOperationTime: Specifies a time as the     *
 *   logical starting point for the change stream.   *
 * allChangesForCluster: (Optional) Whether the      *
 *   stream should include all changes in the        *
 *   cluster.                                        *
 * fullDocument: (Optional) Whether change           *
 *   notifications include a copy of the document    *
 *   when modified by update operations.             *
 { fullDocumentBeforeChange: <string>,
   resumeAfter: <int>,
   showExpandedEvents: <boolean>,
   startAfter: <document>,
   startAtOperationTime: <timestamp>,
   (Optional) allChangesForCluster: <boolean>,
   (Optional) fullDocument: <string>
 }
 */
}''',
    "$changeStreamSplitLargeEvent": '''{
/* Split $changeStream large events into smaller *
 * fragments. Must be the last stage.            *
 {}
 */
}''',
    "$collStats": '''{
/* Return statistics of a collection or view.        *
 * latencyStats: (Optional) Add latency statistics   *
 *   to the return document. Specify "histograms"  *
 *   with true value for additional histogram        *
 *   information.                                    *
 * storageStats: (Optional) Add storage statistics   *
 *   to the return document. Can specify "scale"   *
 *   with a number for scale factor.                 *
 * count: (Optional) Add the total number of         *
 *   documents in the collection to the return       *
 *   document.                                       *
 * queryExecStats: (Optional) Add query execution    *
 *   statistics to the return document.              *
 { (Optional) latencyStats: { histograms: <bool> },
   (Optional) storageStats: { scale: <number> },
   (Optional) count: {},
   (Optional) queryExecStats: {}
 }
 */
}''',
    "$count": '''/* Pass a document to the next stage that contains *\x20
 * a count of the number of documents input to the *\x20
 * stage.                                          *\x20
 * output_field_name: name of the output field.    *
 <output_field_name>
 */''',
    "$currentOp": '''{
/* Return a stream of documents containing         *
 * information on operations and sessions.         *
 * allUsers: (Optional) Whether report on          *
 *   operations belonging to current user or all   *
 *   users.                                        *
 * idleConnections: (Optional) Whether only report *
 *   active operations or report all operations,   *
 *   including idle connections.                   *
 * idleCursors: (Optional) whether report on idle  *
 *   cursors or not.                               *
 * idleSessions: (Optional) whether report on      *
 *   inactive sessions or not.                     *
 * localOps: (Optional) Whether report on only     *
 *   local operations or include operations        *
 *   running on shards.                            *
 * backtrace: (Optional) Whether callstack         *
 *   information is returned as part of the        *
 *   waitingForLatch output field or omitted.      *
 { (Optional) allUsers: <boolean>,
   (Optional) idleConnections: <boolean>,
   (Optional) idleCursors: <boolean>,
   (Optional) idleSessions: <boolean>,
   (Optional) localOps: <boolean>,
   (Optional) backtrace: <boolean>
 }
 */
}''',
    "$densify": '''{
/* Create new documents in a sequence of documents  *
 * where certain values in a field are missing.     *
 * field: the field to densify.                     *
 * range: Specify how the data is densified:        *
 * range.bounds: Range of value to be filled in.    *
 * range.step: The amount to increment the value in *
 *   each document.                                 *
 * range.unit: The unit to apply to the step field. *
 *   required if field is a date.                   *
 * partitionByFields: (Optional) Set of fields to   *
 *   act as the compound key to group the           *
 *   documents.                                     *
 { field: <field_name>,
   range: {
     bounds: <[lower_bound, upper_bound] ||
              "full" || "partition">,
     step: <number>,
     unit: <time_unit>
   },
   (Optional) partitionByFields:
     [<field1>, <field2>, ...]
 }
 */
}''',
    "$documents": '''[
/* Return literal documents from input values. *
 [<document1>, <document2>, ...]
 */
]''',
    "$facet": '''{
/* Process multiple pipelines within a single stage *
 * on the same set of input documents.              *
 * outputField: name of the field the pipeline's    *
 *   output be stored under.                        *
 * pipeline: array of aggregation pipeline stages.  *
 { <outputField1> : <pipeline1>,
   <outputField2> : <pipeline2>,
   ...
 }
 */
}''',
    "$fill": '''{
/* Populate null and missing field values within    *
 * documents.                                       *
 * output: Specify which field(s) to be filled and  *
 *   by either a given value or a specified method. *
 * partitionBy: (Optional) Specify an expression to *
 *   group the documents.                           *
 * partitionByFields: (Optional) Specify an array   *
 *   of fields as the compound key to group the     *
 *   documents. Required if method is specified in  *
 *   at least one output field.                     *
 * sortBy: (Optional) Specify the field(s) to sort  *
 *   the documents within each partiton.            *
 { output:
   { <field1> : { <"value": expression ||
                   "method": string> },
     <field2> : { <"value": expression ||
                   "method": string> },
     ...
   }
   (Optional) partitionBy: <expression>,
   (Optional) partitionByFields:
     [<field1>, <fields>, ...],
   (Optional) sortBy: { <field1> : <sort_order1>,
                        <field2> : <sort_order2>,
                        ...
                      }
 }
 */
}''',
    "$geoNear": '''{
/* Output documents in order of nearest to farthest *
 * from a specified point. Must be the first stage. *
 * distanceField: The name of the output field that *
 *   contains the calculated distance.              *
 * near: The point for which to find the closest    *
 *   documents.                                     *
 * distanceMultiplier: (Optional) Multiply all      *
 *   distances returned by the query with a given   *
 *   factor.                                        *
 * includeLocs: (Optional) The name of the output   *
 *   field that contains the location used to       *
 *   calculate the distance.                        *
 * key: (Optional) Specify the geospatial indexed   *
 *   field to use when calculating the distance.    *
 * maxDistance: (Optional) The maximum distance     *
 *   from the center point that the documents would *
 *   be returned.                                   *
 * minDistance: (Optional) The minimum distance     *
 *   from the center point that the documents would *
 *   be returned.                                   *
 * query: (Optional) Limit the results to the       *
 *   documents that match the query.                *
 * spherical: (Optional) Whether use $nearSphere or *
 *   $near sematics to calculate the distance.      *
 { distanceField: <field_name>,
   near: <GeoJson_point_or_coordinate_pair>,
   (Optional) distanceMultiplier: <number>,
   (Optional) includeLocs: <field_name>,
   (Optional) key: <geospatial_indexed_field>,
   (Optional) maxDistance: <number>,
   (Optional) minDistance: <number>,
   (Optional) query: <document>,
   (Optional) spherical: <boolean>
 }
 */
}''',
    "$graphLookup": '''{
/* Perform a recursive search on a collection, with *
 * options for restricting the search by recursion  *
 * depth and query filter.                          *
 * from: Target collection to search.               *
 * startWith: Expression that specifies the value   *
 *   of the connectFromField to start the recursive *
 *   search with.                                   *
 * connectFromField: Name of the field whose value  *
 *   uses to recursively match against the          *
 *   connectToField of other documents.             *
 * connectionToField: Name of the field in other    *
 *   documents against which to match the value of  *
 *   the connectFromField.                          *
 * as: Name of the array field added to each output *
 *   document, containing the documents transversed *
 *   to reach the document.                         *
 * maxDepth: (Optional) Specify the maximum         *
 *   recursion depth.                               *
 * depthField: (Optional) Name of the field to add  *
 *   to each traversed document, containing the     *
 *   recursion depth for the document.              *
 * restrictSearchWithMatch: (Optional) Additional   *
 *   conditions for the recirsive search.           *
 { from: <collection_name>,
   startWith: <expression>,
   connectFromField: <field_name>,
   connectToField: <field_name>,
   as: <new_field_name>,
   (Optional) maxDepth: <number>,
   (Optional) depthField: <new_field_name>,
   (Optional) restrictSearchWithMatch: <document>
 }
 */
}''',
    "$group": '''{
/* Separate documents into groups according to a  *
 * group key. The output is one document for each *
 * unique group key.                              *
 * _id: Expression specifies the group key.       *
 * field: (Optional) New field(s) with value      *
 *   calculated with accumulator operators.       *
 {
 _id: <expression>,
 (Optional)
   <field1>: { <accumulator1> : <expression1> },
   <field2>: { <accumulator2> : <expression2> },
   ...
 }
 */
}''',
    "$indexStats": '''{
/* Return statistics regarding the of *
 * each index for the collection.     *
 {}
 */
}''',
    "$limit": '''/* Limit the number of documents passed to the     *
 * next stage.                                     *
 * number_of_docs: the maximun number of documents *
 *   to pass to next stage.                        *
 <number_of_docs>
 */''',
    "$listLocalSessions": '''{
/* List the sessions cached in memory by the mongod *
 * or mongos instance. Must be the first stage.     *
 * If run with no param, returns all sessions for   *
 * current user (with access control) or all        *
 * sessions (without access control).               *
 * users: (Optional) Return all sessions for the   *
 *   specified users.                               *
 * allUsers: (Optional) Pass in true to return all *
 *   sessions for all users.                        *
 { (Optional) users: [
                      { user: <user1>, db: <db1> },
                      { user: <user2>, db: <db2> },
                      ...
                     ],
   (Optional) allUsers: true
 }
 */
}''',
    "$listSampledQueries": '''{
/* Return sampled queries for all collections *
 * or a specific collection.                  *
 * namespace: (Optional) List sampled queries *
 *   of specified collection.                 *
 { (Optional) namespace: <collection_name> }
 */
}''',
    "$listSearchIndexes": '''{
/* Return information about existing Atlas Search *
 * indexes on a specified collection.             *
 * id: (Optional) id of the index to return       *
 *   information about.                           *
 * name: (Optional) name of the index to return   *
 *   information about.                           *
 { (Optional) id: <index_id>,
   (Optional) name: <index_name>
 }
 */
}''',
    "$listSessions": '''{
/* List all sessions stored in the system.session   *
 * collection if the config database. Must be the   *
 * first stage in the pipeline.                     *
 * If run with no param, returns all sessions for   *
 * current user (with access control) or all        *
 * sessions (without access control).               *
 * users: (Optional) Return all sessions for the   *
 *   specified users.                               *
 * allUsers: (Optional) Pass in true to return all *
 *   sessions for all users.                        *
 { (Optional) users: [
                      { user: <user1>, db: <db1> },
                      { user: <user2>, db: <db2> },
                      ...
                     ],
   (Optional) allUsers: true
 }
 */
}''',
    "$lookup": '''{
/* Perform a left outer join to a collection in the *
 * same database to filter in documents from the    *
 * "joined" collection for processing.              *
 * form: Specify the foreign collection in the same *
 *   database to join to the local collection.      *
 * localField: Specify the local document's field   *
 *   to perform an equality match with the foreign  *
 *   document's foreignField.                       *
 * foreignField: Specify the foreign document's     *
 *   field to perform an equality match with the    *
 *   local document's localField.                   *
 * let: (Optional) Specify the variables to use in  *
 *   the pipeline stages.                           *
 * pipeline: Specify the pipeline to run on, and    *
 *   returns docuemnt from the foreign collection.  *
 * as: Specify the name of the new array field to   *
 *   add to the input documents, which contains the *
 *   matching documents from the "from" collection. *
 { from: <joined_collection>,
   localField: <field_from_local_document>,
   foreignField: <field_from_foreign_document>,
   *Option* let: { <var1>: <expression1>,
                   <var2>: <expression2>,
                   ...
                 }
   pipeline: <pipeline>,
   as: <output_field_name>
 }
 */
}''',
    "$match": '''{
/* Filter out documents based on given criteria. *
 * query: criteria to filter the documents.      *
 { <query> }
 */
}''',
    "$merge": '''{
/* Write the result of the aggregation pipeline to *
 * a specified collection. Must be the last stage. *
 * into: The output collection.                    *
 * on: (Optional) Field(s) that act as a unique    *
 *   identifier for a document.                    *
 * whenMatched: (Optional) The behaviour if a      *
 *   result document and an existing document in   *
 *   the collection have the same value for the    *
 *   specified on field(s).                        *
 * let: (Optional) Specify variables for use in    *
 *   the whenMatched pipeline, if a pipeline is    *
 *   specified for whenMatched.                    *
 * whenNotMatched: (Optional) The behaviour if a   *
 *   result document does not match an existing    *
 *  document in the out collection.                *
 { into: <collection> ||
         {db: <database>, coll: <collection>},
   (Optional) on: <single_field> ||
                  [ <field1>, <field2>, ...],
   (Optional) whenMatched: "replace" ||
                           "keepExisting" ||
                           "merge" || "fail" ||
                           <pipeline>,
   (Optional) let: { <var1> : <expression1>,
                     <var2> : <expression2>,
                     ...
                   },
   (Optional) whenNotMatched: "insert" ||
                              "discard" || "fail"
 }
 */
}''',
    "$out": '''{
/* Take the result documents of the aggregation   *
 * pipeline and write them to a specified         *
 * collection. Must be the last stage.            *
 * db: the output database name.                  *
 * coll: the output collection name.              *
 * timeseries: (Optional) A document that         *
 *   specifies the configuration to use when      *
 *   writing to a time series collection.         *
 * timeField: Required when writing to a time     *
 *   series collection.                           *
 * metaField: (Optional) Name of the field which  *
 *   contains metadata in each time series        *
 *   document.                                    *
 * granularity: (Optional) Set to the value that  *
 *   most closely matches the time between        *
 *   consecutive incoming timestamps.             *
 * bucketMaxSpanSeconds: (Optional) Set the       *
 *   maximum time between timestamps in the same  *
 *   bucket.                                      *
 * bucketRoundingSeconds: (Optional) Round down   *
 *   the document's timestamp value to set the    *
 *   new bucket's minimum time, when a new bucket *
 *   is required.                                 *
 <output_collection> ||
 { db: <output_database>,
   coll: <output_collection>,
   (Optional) timeseries:
     { timeField: <field_name>,
       (Optional) metaField: <field_name>,
       (Optional) granularity: <"seconds" ||
                                "minutes" ||
                                "hours">,
       (Optional) bucketMaxSpanSeconds: <seconds>,
       (Optional) bucketRoundingSeconds: <seconds>
     }
 }
 */
}''',
    "$planCacheStats": '''{
/* Return plan cache information for a collection. *
 {}
 */
}''',
    "$project": '''{
/* Specify which field(s) of the documents to be    *
 * included or excluded.                            *
 * field: name of field to be included or excluded. *
 * specification: 1/true for including the field,   *
 *   0/false for excluding the field, or custom     *
 *   expression for calculating a new value for     *
 *   the field.                                     *
 { <field1> : <specification1>,
   <field2> : <specification2>,
   ...
 }
 */
}''',
    "$redact": '''{
/* Restrict entire documents or content within      *
 * documents from being outputted based on          *
 * information stored in the documents themselves.  *
 * Argument can be any valid expression as long as  *
 * long as it resolves to one of the below system   *
 * variables:                                       *
 * $$DESCEND: Returns the fields at the current     *
 *   document level, excluding embedded ducments.   *
 * $$PRUNE: Excludes all fields at current document *
 *   or embedded document level, without further    *
 *   inspection of any of the excluded fields.      *
 * $$KEEP: Returns or keeps all fields at current   *
 *   document or embedded document level, without   *
 * further inspection of the fields at this level.  *
 <expression>
 */
}''',
    "$replaceRoot": '''{
/* Replace the input document with the *
 * specified document.                 *
 * newRoot: The replacing document.    *
 { newRoot: <document> }
 */
}''',
    "$replaceWith": '''{
/* Replace the input document with *
 * the specified document.         *
 <replacing_document>
 */
}''',
    "$sample": '''{
/* Randomly select the specified number of   *
 * documents from the input documents.       *
 * size: number of documents to be selected. *
 { size : <number> }
 */
}''',
    "$search": '''{
/* Perform a full-text search of the field(s) in an *
 * Atlas collection. The field(s) must be covered   *
 * by an Atlas Search index. Must be the first      *
 * stage in the pipeline.                           *
 * operator_name//collector_name: Name of the        *
 *   operator//collector to use with the query.      *
 * specification: Options for the specified         *
 *   operator//collector.                            *
 * concurrent: (Optional) Parallelize search across *
 *   segment on dedicated search nodes.             *
 * count: (Optional) Count options for retriving a  *
 *   count of the results.                          *
 * highlight: (Optional) Highlighting options for   *
 *   display search terms in original context.      *
 * index: (Optional) Name of the Atlas Search index *
 *   to use.                                        *
 * returnStoredSource: (Optional) Whether to        *
 *   perform a full document lookup on the backend  *
 *   database or return only stored source fields   *
 *   directly from Atlas Search.                    *
 * searchBefore: (Optional) A reference point that  *
 *   search will immediately return documents       *
 *   before.                                        *
 * searchAfter: (Optional) A reference point that   *
 *   search will immediately return documents       *
 *   after.                                         *
 * scoreDetails: (Optional) Whether to retrieve a   *
 *   detailed breakdown of the score for the        *
 *   documents in the results.                      *
 * sort: (Optional) Fields to sort the results in   *
 *   ascending or descending order.                 *
 * tracking: (Optional) Tracking option to retrieve *
 *   analytics information on the search terms.     *
 { <operator_name||collector_name>:
     <specifications>,
   (Optional) concurrent: <boolean>,
   (Optional) count: <count_options>,
   (Optional) highlight: <highlight_options>,
   (Optional) index: <index_name>,
   (Optional) returnStoredSource: <boolean>,
   (Optional) searchBefore: <encoded_token>,
   (Optional) searchAfter: <encoded_token>,
   (Optional) scoreDetails: <boolean>,
   (Optional) sort: <sort_option>,
   (Optional) tracking: <tracking_option>
 }
 */
}''',
    "$searchMeta": '''{
/* Return different types of metadata result        *
 * documents for Atlas Search queries on the        *
 * field(s) in an Atlas collection. The field(s)    *
 * must be covered by an Atlas Search index.        *
 * Must be the first stage in the pipeline.         *
 * operator_name//collector_name: Name of the        *
 *   operator//collector to use with the query.      *
 * specification: Options for the specified         *
 *   operator//collector.                            *
 * count: (Optional) Count options for retriving a  *
 *   count of the results.                          *
 * index: (Optional) Name of the Atlas Search index *
 *   to use.                                        *
 { <operator_name||collector_name>:
     <specifications>,
   (Optional) count: <count_options>,
   (Optional) index: <index_name>
 }
 */
}''',
    "$set": '''{
/* Add new fields to documents.              *
 * new_field: name of the field to be added. *
 * field_value: expression that defines the  *
 *   value of the added field.               *
 { <new_field1>: <field_value1>,
   <new_field2>: <field_value2>,
   ...
 }
 */
}''',
    "$setWindowFields": '''{
/* Perform operations on a specified span of        *
 * documents in a collection, known as a window,    *
 * and return the results based on the chosen       *
 * window operator.                                 *
 * partitionBy: (Optional) Sepcify an expression to *
 *   group the documents.                           *
 * sortBy: (Optional) Specify the field(s) to sort  *
 *   the documents by in the partition.             *
 * output: Specify the field(s) to append to the    *
 *   output documents. Each field is set to the     *
 *   result returned by the window operator.        *
 * window: (Optional) Specify the window boundaries *
 *   and parameters.                                *
 * documents: (Optional) A window where the lower   *
 *   and upper boundaries are specified relative to *
 *   the position of the current document.          *
 * range: (Optional) A window where the lower and   *
 *   upper boundaries are defined using a range of  *
 *   values based on the sortBy field.              *
 * unit: (Optional) specify the units for time      *
 *   range window boundaries.                       *
 { (Optional) partitionBy: <expression>,
   (Optional) sortBy: { <field1>, <sort_order1>,
                        <field2>, <sort_order2>,
                        ...
                      },
   output: {
     <output_field1>: {
       <window_operator>: <parameters>,
       (Optional) window: {
         (Optional) documents: [ <lower_bound>,
                                 <upper_bound> ],
         (Optional) range: [ <lower_bound>,
                             <upper_bound> ],
         (Optional) unit: <time_unit>
       }
     },
     <output_field2>: {...},
     ...
   }
 }
 */
}''',
    "$sharedDataDistribution": '''{
/* Return information on the distribution *
 * of data in shared collections.         *
 {}
 */
}''',
    "$skip": '''/* Skip over the specified number of documents and *
 * pass the remaining documents to the next stage. *
 <number_of_skipping_documents>
*/''',
    "$sort": '''{
/* Sort the documents by specified sort order(s).  *
 * field: name of field to sort documents on.      *
 * sort_order: 1 for ascending, -1 for descending, *
 *   or { $meta:"textScore" } for sorting by     *
 *   the computed textScore metadata.              *
 { <field1> : <sort_order1>,
   <field2> : <sort_order2>,
   ...
 }
 */
}''',
    "$sortByCount": '''/* Group incoming documents based on the value of a *
 * specified expression, the compute the count of   *
 * each documents in each distinct group.           *
 <expression>
 */''',
    "$unionWith": '''{
/* Perform an union of two collections.          *
 * coll: The collection whose pipeline results   *
 *   would be included in the result set.        *
 * pipeline: (Optional) Aggregation pipeline to  *
 *   apply to the specified collection.          *
 <collection_to_union> ||
 { coll: <collection_to_union>,
   (Optional) pipeline: <pipeline>
 }
 */
}''',
    "$unset": '''/* Exclude fields from documents.           *
 * field: name of the field to be excluded. *
 <field> ||
 [ <field1>, <field2>, ... ]
*/''',
    "$unwind": '''{
/* Deconstruct an array field from the input        *
 * documents to output a document for each element. *
 * path: Field path to an array field.              *
 * includeArrayIndex: (Optional) The name of a new  *
 *   field to hold the array index of the element.  *
 * preserveNullAndEmptyArrays: (Optional) whether   *
 *   output document for null, missing, or empty    *
 *   array or not.                                  *
 <field_path> ||
 { path: <field_path>,
   (Optional) includeArrayIndex: <new_field_name>,
   (Optional) preserveNullAndEmptyArrays: <boolean> }
 */
}''',
}
