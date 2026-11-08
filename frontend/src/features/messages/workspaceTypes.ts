import type { Filters, FilterOptions, ViewMode, PreviewAction, ActionPreview, Message, AttachmentSummary, MessageDetail } from "../../shared/contracts";

export type WorkspaceProps = {
  externalMessage?: Pick<Message,"id"|"account_id"|"group_id"|"is_deleted">;
  filters: Filters;
  appliedFilters?: Filters;
  options: FilterOptions;
  updateFilter: <K extends keyof Filters>(key: K, value: Filters[K]) => void;
  applySearch: (next?: Filters) => void;
  resetSearch: () => void;
  viewMode: ViewMode;
  deleting: boolean;
  previewAction: (action: PreviewAction) => Promise<void>;
  actionPreview: ActionPreview | null;
  executeSoftDelete: () => Promise<void>;
  executeRestore: () => Promise<void>;
  executeHardDelete: () => Promise<void>;
  loading: boolean;
  messages: Message[];
  total: number;
  attachmentSummary: AttachmentSummary;
  selectedMessageId: number | null;
  setSelectedMessageId: (id: number) => void;
  loadMore: () => void;
  hasMore: boolean;
  loadingMore: boolean;
  detailLoading: boolean;
  detail: MessageDetail | null;
};
