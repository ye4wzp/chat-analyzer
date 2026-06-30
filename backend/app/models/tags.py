from typing import Optional

from pydantic import BaseModel


class TagCreate(BaseModel):
    name: str
    color: Optional[str] = None


class TagUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    status: Optional[str] = None  # set 'active' to approve a pending AI tag


class TagBatchRequest(BaseModel):
    include_groups: bool = False  # default: only 1:1 contacts (好友), skip group chats
    only_untagged: bool = True    # skip contacts that already have confirmed tags
    msg_limit: int = 100          # most-recent messages per contact fed to the LLM
    max_contacts: Optional[int] = None


class LinkIdsRequest(BaseModel):
    link_ids: list[int]


class ContactTagAdd(BaseModel):
    tag_id: Optional[int] = None   # use an existing tag...
    name: Optional[str] = None     # ...or create-or-get one by name
    color: Optional[str] = None


class TagVipRequest(BaseModel):
    action: str = "add"  # 'add' | 'remove' — sync this tag's contacts to vip_contacts


class TagInsightRequest(BaseModel):
    max_contacts: int = 20
    msg_limit: int = 30  # most-recent messages per contact
