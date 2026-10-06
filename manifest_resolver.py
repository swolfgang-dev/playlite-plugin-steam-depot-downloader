"""Fill missing Steam manifests from providers without accepting stale versions."""
from dataclasses import replace
from .app_info import ordered_content,matches_platform,manifest_id,build_plan
from .providers import SOURCES


class ManifestResolver:
    def __init__(self,preferred,fetch,progress=lambda message:None,limit=128):
        self.providers=[preferred,*[source for source in SOURCES if source!=preferred]]
        self.fetch=fetch;self.progress=progress;self.limit=limit
        self.cache={};self.errors={};self.used={};self.requests=0

    def pack(self,source,app):
        key=(source,app)
        if key not in self.cache:
            if self.requests>=self.limit:raise ValueError('Manifest lookup request limit reached. Nothing was queued.')
            self.requests+=1;self.progress(f'Checking {source} manifests · App {app}')
            try:self.cache[key]=list(self.fetch(source,app))
            except Exception as error:
                self.cache[key]=[];self.errors[key]=str(error)
        return self.cache[key]

    def first_pack(self,app):
        for source in self.providers:
            rows=self.pack(source,app)
            if rows:self.first_source=source;return rows
        return []

    def prepare(self,info,base_rows,platform,selected,language='english',architecture='64',branch='public',base_source=None):
        app=info['game']['id']
        if base_rows:self.cache.setdefault((base_source or self.providers[0],app),list(base_rows))
        resolved=[]
        for entry,key,node in ordered_content(info,selected):
            if not matches_platform(node,platform,language,architecture):continue
            expected=manifest_id(node,branch)
            if expected is None and not node.get('depotfromapp'):continue
            # A DLC manifest may be supplied in its own pack or the parent pack.
            owners=list(dict.fromkeys([app,int(node.get('_source_app',entry['id']))]))
            found=None;attempts=[]
            for source in self.providers:
                for owner in owners:
                    for row in self.pack(source,owner):
                        if row.id==key and (expected is None or row.manifest==expected or row.manifest is None and row.key):
                            candidate=replace(row,manifest=expected) if row.manifest is None and expected is not None else row
                            if found is None:
                                found=candidate;self.used[key]=source
                            elif candidate.data:
                                found=replace(candidate,key=candidate.key or found.key);self.used[key]=source
                            if found.data:break
                    if found and found.data:break
                    error=self.errors.get((source,owner))
                    attempts.append(f'{source} / App {owner}: {error or "matching manifest unavailable"}')
                if found and found.data:break
            if not found:
                title=entry['name'] or f'DLC {entry["id"]}'
                detail='; '.join(attempts)
                message=f'No provider supplied the required Steam manifest for depot {key} ({title}). {detail}. Nothing was queued.'
                entry['manifest_error']=message;entry['manifest_provider']=self.providers[0]
                self.progress(detail)
                raise ValueError(f'Missing matching manifest for depot {key}. Checked {", ".join(self.providers)}. Nothing was queued; see Downloader log for provider errors.')
            resolved.append(found)
            entry.pop('manifest_error',None);entry.pop('manifest_provider',None)
        # Only exact matching rows are merged, so a stale pack cannot replace them.
        return build_plan(info,{app:resolved},platform,selected,language,architecture,branch)
