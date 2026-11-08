export type Account = {
  id: number;
  display_name: string;
  auth_type: string;
  login_profile_name: string | null;
  is_active: number;
  cookie_profile?: CookieProfileStatus;
};

export type CookieProfileStatus = {
  account_id: number;
  exists: boolean;
  authenticated: boolean;
  cookie_count: number;
  updated_at: string | null;
};

export type Group = {
  id: number;
  account_id: number;
  name: string;
  source_group_id: string | null;
  account_name: string;
  is_active: number;
};

export type User = {
  id: number;
  display_name: string;
  source_user_id: string | null;
};

export type FilterOptions = {
  accounts: Account[];
  groups: Group[];
  users: User[];
  message_types: string[];
};

export type Archive = {id: number; message_id: number; source_url: string; kind: string; status: string; pending_resources?: number};

export type Message = {
  reference_method?: "manual" | "source_id" | null;
  archives?: Archive[];
  link_previews?: LinkPreview[];
  id: number;
  account_id: number;
  account_name: string;
  group_id: number;
  group_name: string;
  user_id: number;
  sender_name: string;
  source_user_id: string | null;
  source_message_id: string | null;
  sent_at: string;
  message_type: string;
  content_text: string;
  is_deleted: number;
  deleted_at: string | null;
  attachment_count: number;
};

export type LinkPreview = {url: string; author: string | null; title: string | null; text: string | null;
  reposts: number | null; comments: number | null; likes: number | null};

export type Attachment = {
  id: number;
  attachment_type: string;
  source_url: string | null;
  local_path: string | null;
  file_name: string | null;
  mime_type: string | null;
  file_size: number | null;
  title: string | null;
  description: string | null;
  download_status: string;
};

export type AttachmentSummary = {
  total_count: number;
  message_count: number;
  by_type: Record<string, number>;
};

export type MessageDetail = Message & {
  raw_payload: string | null;
  normalized_text: string | null;
  attachments: Attachment[];
};

export type Cursor = {
  before_sent_at: string;
  before_id: number;
};

export type Filters = {
  viewer?: string;
  accountId: string;
  groupId: string;
  user: string;
  keyword: string;
  dateFrom: string;
  dateTo: string;
  messageType: string;
  hasAttachment: string;
};

export type DeletePreview = {
  preview_count: number;
  delete_mode: string;
  restore_mode?: string;
  attachment_record_count?: number;
};

export type PreviewAction = "soft-delete" | "restore" | "hard-delete";
export type ActionPreview = DeletePreview & {
  action: PreviewAction;
};

export type ViewMode = "dashboard" | "logs" | "ocr" | "accounts" | "groups" | "active" | "deleted" | "jobs" | "monitor" | "tools" | "weibo";

export type CollectionJob = {
  id: number;
  account_id: number;
  account_name: string;
  group_id: number;
  group_name: string;
  range_start: string;
  range_end: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  total_seen_count: number;
  inserted_count: number;
  skipped_count: number;
  failed_count: number;
  error_message: string | null;
  collector_type: string;
  created_at: string;
  next_max_mid: string | number | null;
  checkpoint_oldest_at: string | null;
  page_count: number;
  attempt_count: number;
  duplicate_count: number;
  filtered_red_packet_count: number;
  filtered_system_notice_count: number;
  stop_code: string | null;
  stop_reason: string | null;
  last_http_status: number | null;
  last_error_code: string | null;
  stop_requested_at: string | null;
  resume_not_before: string | null;
  last_progress_at: string | null;
  queue_position: number | null;
};

export type CollectionJobAttempt = {
  id: number;
  job_id: number;
  attempt_no: number;
  status: string;
  start_max_mid: string;
  end_max_mid: string | null;
  started_at: string;
  finished_at: string | null;
  page_count: number;
  total_seen_count: number;
  inserted_count: number;
  skipped_count: number;
  duplicate_count: number;
  filtered_red_packet_count: number;
  filtered_system_notice_count: number;
  failed_count: number;
  stop_code: string | null;
  stop_reason: string | null;
  last_http_status: number | null;
  last_error_code: string | null;
};

export type CollectionJobForm = {
  accountId: string;
  groupId: string;
  rangeStart: string;
  rangeEnd: string;
  sourceFile: string;
};

export type ApiTargetForm = {
  accountName: string;
  groupName: string;
  sourceGroupId: string;
};

export type ImportFile = {
  name: string;
  relative_path: string;
  size: number;
  modified_at: string;
};

export type SingleGroupCollectionSummary = {
  collection_job_id: number;
  source_total_count: number;
  total_count: number;
  inserted_count: number;
  skipped_count: number;
  red_packet_count: number;
  filtered_system_notice_count: number;
  duplicate_count: number;
  attachment_count: number;
  out_of_range_count: number;
  invalid_count: number;
};

export type BrowserCapture = {
  id: number;
  account_id: number;
  account_name: string;
  group_id: number;
  group_name: string;
  range_start: string;
  range_end: string;
  page_url: string | null;
  page_title: string | null;
  captured_at: string;
  text_length: number;
  script_version: string;
  status: string;
  created_at: string;
};

export type BrowserCaptureParseItem = {
  index: number;
  sent_at: string;
  sender_name: string;
  message_type: string;
  content_text: string;
  source_message_id: string;
  is_duplicate: boolean;
  block_top: number | null;
};

export type BrowserCaptureParsePreview = {
  capture_id: number;
  parser_version: string;
  total_blocks: number;
  skipped_blocks: number;
  parsed_count: number;
  duplicate_count: number;
  items: BrowserCaptureParseItem[];
};

export type BrowserCaptureImportResult = {
  capture_id: number;
  parsed_count: number;
  inserted_count: number;
  skipped_count: number;
  duplicate_count: number;
  red_packet_count: number;
  filtered_system_notice_count: number;
  collection_job: CollectionJob;
};

export type BrowserLoginSessionStatus =
  | "launching_browser"
  | "awaiting_scan"
  | "login_detected"
  | "cookie_saved"
  | "awaiting_group_selection"
  | "group_candidate_found"
  | "binding_confirmed"
  | "completed"
  | "cancelled"
  | "timed_out"
  | "failed";

export type BrowserLoginSession = {
  session_id: string;
  account_id: number;
  group_id: number | null;
  status: BrowserLoginSessionStatus;
  created_at: string;
  expires_at: string;
  cookie_ready: boolean;
  candidate_source_group_id: string | null;
  candidate_captured_at: string | null;
  error_code: string | null;
  error_message: string | null;
};

export type VerificationBooleanField =
  | "can_login"
  | "can_view_group"
  | "can_view_history"
  | "can_page_history"
  | "can_access_images"
  | "can_access_files"
  | "can_access_links"
  | "red_packet_identified"
  | "rate_limit_observed";

export type WeiboObservation = {
  id: number;
  report_id: number;
  observation_type: string;
  method: string | null;
  endpoint_path: string | null;
  request_fields: unknown;
  response_fields: unknown;
  sample_payload: unknown;
  pagination_fields: unknown;
  redaction_notes: string | null;
  created_at: string;
};

export type WeiboVerificationReport = {
  id: number;
  account_id: number;
  account_name: string;
  group_id: number;
  group_name: string;
  can_login: boolean;
  can_view_group: boolean;
  can_view_history: boolean;
  history_days_checked: number | null;
  can_page_history: boolean;
  can_access_images: boolean;
  can_access_files: boolean;
  can_access_links: boolean;
  red_packet_identified: boolean;
  rate_limit_observed: boolean;
  risk_level: "unknown" | "low" | "medium" | "high";
  verification_status: "draft" | "verified" | "blocked";
  notes: string | null;
  observation_count?: number;
  observations?: WeiboObservation[];
  created_at: string;
  updated_at: string;
};

export type VerificationReportForm = {
  accountId: string;
  groupId: string;
  historyDaysChecked: string;
  riskLevel: "unknown" | "low" | "medium" | "high";
  verificationStatus: "draft" | "verified" | "blocked";
  notes: string;
};

export type ObservationForm = {
  observationType:
    | "message_history"
    | "pagination"
    | "image"
    | "file"
    | "link"
    | "red_packet"
    | "error"
    | "official_api";
  method: string;
  endpointPath: string;
  requestFields: string;
  responseFields: string;
  samplePayload: string;
  paginationFields: string;
  redactionNotes: string;
};
