"""Immutable raw OpenAIP archive; archive completion is required before import."""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class ArchiveManifest:
    source:str; input_sha256:str; pages:tuple[dict[str,Any],...]; fabricated:bool=False
    def bytes(self)->bytes:return json.dumps({"source":self.source,"input_sha256":self.input_sha256,"pages":self.pages,"fabricated":self.fabricated},sort_keys=True,separators=(",",":"),default=str).encode()

class AirspaceArchive:
    def __init__(self,client:Any,bucket:str="garuda-airspace-raw")->None:self.client,self.bucket=client,bucket
    def archive_pages(self,pages:list[bytes],*,source:str="openaip",fabricated:bool=False)->ArchiveManifest:
        digest=hashlib.sha256(b"".join(pages)).hexdigest(); entries=[]
        for number,page in enumerate(pages,1):
            key=f"raw/openaip/{digest}/page-{number}.json"; page_hash=hashlib.sha256(page).hexdigest()
            self.client.put_object(Bucket=self.bucket,Key=key,Body=page,Metadata={"sha256":page_hash,"source":source})
            entries.append({"key":key,"sha256":page_hash,"bytes":len(page)})
        manifest=ArchiveManifest(source,digest,tuple(entries),fabricated); self.client.put_object(Bucket=self.bucket,Key=f"raw/openaip/{digest}/manifest.json",Body=manifest.bytes(),Metadata={"sha256":hashlib.sha256(manifest.bytes()).hexdigest()})
        return manifest
    def replay(self,input_sha256:str)->list[bytes]:
        manifest=json.loads(self.client.get_object(Bucket=self.bucket,Key=f"raw/openaip/{input_sha256}/manifest.json")["Body"].read())
        pages=[]
        for item in manifest["pages"]:
            value=self.client.get_object(Bucket=self.bucket,Key=item["key"])["Body"].read()
            if hashlib.sha256(value).hexdigest()!=item["sha256"]: raise ValueError("archived page checksum mismatch")
            pages.append(value)
        return pages
