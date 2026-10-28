// Test-driver Python only: logical authority lengths are not retained allocations.
// Hash the actual accepted replayable authority through bounded reads and EOF.
export const sourceAuthorityPython = `
def _source_authority_observation(w):
 import hashlib
 source=getattr(w,'source',None)
 storage=dict(kind='none',backing_kind='none',authority_bytes=0,owned_source_payload_bytes=0)
 replay=dict(bytes=0,sha256=None,max_chunk_bytes=0,read_calls=0,eof_verified=False)
 if source is not None:
  provider=source.input;backing=provider;owned=0
  while True:
   data=getattr(backing,'data',None)
   if isinstance(data,bytes):owned+=len(data)
   nested=getattr(backing,'authority',None)
   if nested is None:break
   backing=nested
  storage=dict(kind=type(provider).__name__,backing_kind=type(backing).__name__,authority_bytes=source.byte_length,owned_source_payload_bytes=owned)
  digest=hashlib.sha256()
  with source.open() as stream:
   while True:
    chunk=stream.read(65536);replay['read_calls']+=1
    if not chunk:break
    digest.update(chunk);replay['bytes']+=len(chunk)
    replay['max_chunk_bytes']=max(replay['max_chunk_bytes'],len(chunk))
  replay.update(sha256=digest.hexdigest(),eof_verified=True)
  if replay['bytes']!=source.byte_length or replay['sha256']!=w.source_sha256:
   raise AssertionError('Accepted source authority identity differs at verified EOF')
 return dict(original_bytes=storage['authority_bytes'],source_storage=storage,source_replay=replay,
  source_bytes_caveat='original_bytes and immutable_original_bytes are logical replayable authority lengths, not retained Python allocations; owned_source_payload_bytes counts explicit byte backing only.')
`;
