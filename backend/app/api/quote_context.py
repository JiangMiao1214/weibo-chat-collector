import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..database import get_connection
from ..services.quote_context import find_quote_context, scoped_source


router = APIRouter(prefix="/api/messages", tags=["messages"])


@router.get("/{message_id}/quote-context")
def quote_context(
    message_id: int = Path(ge=1),
    account_id: int = Query(ge=1),
    group_id: int = Query(ge=1),
    deleted_only: bool = False,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    # One consistent read snapshot; preserve any outer transaction owned by the caller.
    connection.execute("SAVEPOINT quote_context_read")
    try:
        source = scoped_source(connection, message_id, account_id, group_id, deleted_only)
        if source is None:
            raise HTTPException(status_code=404, detail="消息不存在或不属于当前账号、群聊及删除状态")
        return find_quote_context(connection, source)
    finally:
        connection.execute("RELEASE SAVEPOINT quote_context_read")
