"""Scope-bound explicit references, with conservative full-text fallback."""
from .message_references import reference_candidates, enrich_references
import re
import sqlite3


# Match JavaScript String.trim, not Python's slightly wider default whitespace set.
TRIM_CHARS = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
CONTEXT_RADIUS = 5


def text_key(text: str | None) -> str:
    return (text or "").replace("\r\n", "\n").strip(TRIM_CHARS)


def quoted_text(content: str) -> str | None:
    text = content.strip(TRIM_CHARS)
    if not text.startswith("「"):
        return None
    depth = 0
    for index, char in enumerate(text):
        if char == "「":
            depth += 1
        elif char == "」":
            depth -= 1
        if depth == 0:
            quote = text_key(text[1:index])
            separator = re.match(r"^(?:\s*-\s*){3,}", text[index + 1:])
            return quote if separator and quote else None
    return None


def scoped_source(connection, message_id, account_id, group_id, deleted_only):
    return connection.execute(
        "SELECT * FROM messages WHERE id=? AND account_id=? AND group_id=? AND is_deleted=?",
        (message_id, account_id, group_id, int(deleted_only)),
    ).fetchone()


def message_items(connection, ids):
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(f"""
        SELECT m.id, m.account_id, wa.display_name AS account_name,
               m.group_id, cg.name AS group_name, m.user_id, cu.display_name AS sender_name,
               cu.source_user_id, m.source_message_id, m.sent_at, m.message_type,
               m.content_text, m.is_deleted, m.deleted_at,
               (SELECT COUNT(*) FROM attachments a WHERE a.message_id=m.id) AS attachment_count
        FROM messages m
        JOIN weibo_accounts wa ON wa.id=m.account_id
        JOIN chat_groups cg ON cg.id=m.group_id
        JOIN chat_users cu ON cu.id=m.user_id
        WHERE m.id IN ({placeholders}) ORDER BY m.sent_at DESC, m.id DESC
    """, ids).fetchall()
    return enrich_references(connection, [dict(row) for row in rows])


def neighboring_ids(connection, target, direction):
    # These SQL fragments are internal constants, never request strings.
    operator, order = ("<", "DESC") if direction == "older" else (">", "ASC")
    return [row[0] for row in connection.execute(f"""
        SELECT id FROM messages
        WHERE account_id=? AND group_id=? AND is_deleted=?
          AND (sent_at {operator} ? OR (sent_at=? AND id {operator} ?))
        ORDER BY sent_at {order}, id {order} LIMIT ?
    """, (target["account_id"], target["group_id"], target["is_deleted"],
          target["sent_at"], target["sent_at"], target["id"], CONTEXT_RADIUS + 1))]


def find_quote_context(connection: sqlite3.Connection, source: sqlite3.Row) -> dict:
    result = {"source_message_id": source["id"], "account_id": source["account_id"],
              "group_id": source["group_id"], "deleted_only": bool(source["is_deleted"]),
              "match_method": "full_text", "status": "not_quote", "target": None,
              "items": [], "has_older": False, "has_newer": False}
    permanent = reference_candidates(connection, source)
    if permanent:
        result["match_method"], candidates = permanent
    else:
        quote = quoted_text(source["content_text"] or "")
        if quote is None:
            return result
        connection.create_function("quote_text_key", 1, text_key, deterministic=True)
        candidates = connection.execute("""
            SELECT id, account_id, group_id, is_deleted, sent_at FROM messages
            WHERE account_id=? AND group_id=? AND is_deleted=? AND id<>?
              AND julianday(sent_at) < julianday(?) AND quote_text_key(content_text)=?
            LIMIT 2
        """, (source["account_id"], source["group_id"], source["is_deleted"],
              source["id"], source["sent_at"], quote)).fetchall()
    if len(candidates) != 1:
        result["status"] = "ambiguous" if candidates else "not_found"
        return result
    target = candidates[0]
    older = neighboring_ids(connection, target, "older")
    newer = neighboring_ids(connection, target, "newer")
    items = message_items(connection, [target["id"], *older[:CONTEXT_RADIUS], *newer[:CONTEXT_RADIUS]])
    target_item = next(item for item in items if item["id"] == target["id"])
    attachments = connection.execute("SELECT * FROM attachments WHERE message_id=? ORDER BY id", (target["id"],)).fetchall()
    result.update(status="matched", items=items,
                  target={**target_item, "attachments": [dict(row) for row in attachments]},
                  has_older=len(older) > CONTEXT_RADIUS, has_newer=len(newer) > CONTEXT_RADIUS)
    return result
