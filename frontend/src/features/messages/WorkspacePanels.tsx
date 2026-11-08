import {QuoteLookup} from "./QuoteLookup";
import {splitQuote} from "./presentation";
import React from "react";
import { Trash2 } from "lucide-react";
import { formatMessageType } from "./model";
import { AttachmentList } from "./AttachmentList";
import type { WorkspaceProps } from "./workspaceTypes";

export function BatchActions({viewMode, deleting, previewAction, actionPreview, executeSoftDelete, executeRestore, executeHardDelete}: WorkspaceProps) {
  return <>          {viewMode === "active" ? (
            <section className="delete-box">
              <h3>批量软删除</h3>
              <p>删除对象为当前搜索结果，默认只标记删除，不移除附件原件。</p>
              <button
                className="danger-outline-button"
                type="button"
                onClick={() => void previewAction("soft-delete")}
                disabled={deleting}
              >
                <Trash2 size={15} aria-hidden="true" />
                预览删除数量
              </button>
              {actionPreview?.action === "soft-delete" ? (
                <div className="delete-preview">
                  <strong>{actionPreview.preview_count}</strong>
                  <span>条消息将被软删除</span>
                  <button
                    className="danger-button"
                    type="button"
                    onClick={executeSoftDelete}
                    disabled={deleting || actionPreview.preview_count <= 0}
                  >
                    确认软删除
                  </button>
                </div>
              ) : null}
            </section>
          ) : (
            <section className="delete-box recycle-box">
              <h3>回收站操作</h3>
              <p>恢复会让消息重新回到普通列表。彻底删除只删除数据库记录和附件记录，不删除磁盘原件。</p>
              <div className="stacked-buttons">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => void previewAction("restore")}
                  disabled={deleting}
                >
                  预览恢复数量
                </button>
                <button
                  className="danger-outline-button"
                  type="button"
                  onClick={() => void previewAction("hard-delete")}
                  disabled={deleting}
                >
                  预览彻底删除
                </button>
              </div>
              {actionPreview?.action === "restore" ? (
                <div className="delete-preview restore-preview">
                  <strong>{actionPreview.preview_count}</strong>
                  <span>条消息将被恢复</span>
                  <button
                    className="icon-button"
                    type="button"
                    onClick={executeRestore}
                    disabled={deleting || actionPreview.preview_count <= 0}
                  >
                    确认恢复
                  </button>
                </div>
              ) : null}
              {actionPreview?.action === "hard-delete" ? (
                <div className="delete-preview">
                  <strong>{actionPreview.preview_count}</strong>
                  <span>
                    条消息和 {actionPreview.attachment_record_count ?? 0} 条附件记录将被彻底删除
                  </span>
                  <button
                    className="danger-button"
                    type="button"
                    onClick={executeHardDelete}
                    disabled={deleting || actionPreview.preview_count <= 0}
                  >
                    确认彻底删除
                  </button>
                </div>
              ) : null}
            </section>
          )}</>;
}

export function MessageDetails({detail, detailLoading, onClose}: WorkspaceProps & {onClose?: () => void}) {
  return (        <aside className="detail-panel">
          <div className="panel-title">
            <h2>消息详情</h2>
            <span>{detailLoading ? "加载中" : detail ? `#${detail.id}` : "未选择"}</span>
            {onClose && <button type="button" className="reader-detail-close" onClick={onClose}>关闭详情</button>}
          </div>

          {detail ? (
            <div className="detail-body">
              <dl>
                <div>
                  <dt>账号</dt>
                  <dd>{detail.account_name}</dd>
                </div>
                <div>
                  <dt>群聊</dt>
                  <dd>{detail.group_name}</dd>
                </div>
                <div>
                  <dt>用户</dt>
                  <dd>{detail.sender_name}</dd>
                </div>
                <div>
                  <dt>时间</dt>
                  <dd>{detail.sent_at}</dd>
                </div>
                <div>
                  <dt>类型</dt>
                  <dd>{formatMessageType(detail.message_type)}</dd>
                </div>
              </dl>

              <section>
                <h3>正文</h3>
                <p className="detail-text">{detail.content_text || "(无文本内容)"}</p>
                {(splitQuote(detail.content_text) || detail.reference_method) && <QuoteLookup source={detail} label="管理引用关系" />}
              </section>

              <section>
                <h3>附件</h3>
                {detail.attachments.length > 0 ? (
                  <AttachmentList attachments={detail.attachments} message={detail} />
                ) : (
                  <p className="muted">无附件</p>
                )}
              </section>
            </div>
          ) : (
            <div className="empty-state">请选择一条消息</div>
          )}
        </aside>);
}
