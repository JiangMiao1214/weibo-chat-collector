import { CollectionDateTime } from "./features/collection/CollectionDateTime";
import {InsightsPage} from "./features/insights/InsightsPage";
import type {OpenMessages} from "./features/insights/model";
import type {MessageRef} from "./features/messages/ReaderNavigation";
import { ManagementPage } from "./features/management/ManagementPage";
import {normalizeDateHours} from './features/messages/viewerModel';
import {retainExistingTarget,retainExistingFilters,hasMultipleGroups,bindingDisabled} from './features/management/selection';
import {GroupLimitNotice} from './features/management/GroupLimitNotice';
import { ViewNavigation } from "./shared/ViewNavigation";
import type { Account, CookieProfileStatus, Group, FilterOptions, Message, AttachmentSummary, MessageDetail, Cursor, Filters, ViewMode, CollectionJob, CollectionJobAttempt, CollectionJobForm, ApiTargetForm, ImportFile, SingleGroupCollectionSummary, BrowserCapture, BrowserCaptureParsePreview, BrowserCaptureImportResult, BrowserLoginSessionStatus, BrowserLoginSession, VerificationBooleanField, WeiboVerificationReport, VerificationReportForm, ObservationForm } from "./shared/contracts";
import { API_BASE_URL, fetchJson } from "./shared/http";
import {useBatchActions} from "./features/messages/useBatchActions";
import { useMessageData } from "./features/messages/useMessageData";
import { MessageWorkspace } from "./features/messages/MessageWorkspace";
import { terminalBrowserSessionStatuses, useBrowserLoginPolling } from "./features/login/useBrowserLoginPolling";
import { PAGE_SIZE, emptyFilters, emptyAttachmentSummary, buildMessageQuery, formatMessageType } from "./features/messages/model";
import React, { useEffect, useMemo, useState } from "react";
import {
  CalendarDays,
  Clock,
  Database,
  LogIn,
  Plus,
  RefreshCw,
  Save,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import "./styles.css";

const COLLECTION_JOB_PAGE_SIZE = 50;

const emptyCollectionJobForm: CollectionJobForm = {
  accountId: "",
  groupId: "",
  rangeStart: "",
  rangeEnd: "",
  sourceFile: "",
};

const emptyApiTargetForm: ApiTargetForm = {
  accountName: "",
  groupName: "",
  sourceGroupId: "",
};

const emptyVerificationReportForm: VerificationReportForm = {
  accountId: "",
  groupId: "",
  historyDaysChecked: "7",
  riskLevel: "unknown",
  verificationStatus: "draft",
  notes: "",
};

const emptyObservationForm: ObservationForm = {
  observationType: "message_history",
  method: "GET",
  endpointPath: "",
  requestFields: "",
  responseFields: "",
  samplePayload: "",
  paginationFields: "",
  redactionNotes: "已移除 Cookie、Token、Authorization、用户隐私字段。",
};

const verificationBooleanFields: Array<{ key: VerificationBooleanField; label: string }> = [
  { key: "can_login", label: "账号可稳定登录" },
  { key: "can_view_group", label: "账号可进入对应群聊" },
  { key: "can_view_history", label: "可查看历史消息" },
  { key: "can_page_history", label: "历史消息可翻页" },
  { key: "can_access_images", label: "图片字段可识别" },
  { key: "can_access_files", label: "文件字段可识别" },
  { key: "can_access_links", label: "链接字段可识别" },
  { key: "red_packet_identified", label: "红包消息特征可识别" },
  { key: "rate_limit_observed", label: "观察到验证或风控限制" },
];

function formatDateTimeLocal(value: string): string {
  if (!value) return "";
  const normalized = value.replace("T", " ");
  return normalized.length === 16 ? `${normalized}:00` : normalized;
}

function isFutureUtcTimestamp(value: string | null): boolean {
  if (!value) return false;
  const normalized = value.includes("T") ? value : value.replace(" ", "T");
  const utcValue = /(?:Z|[+-]\d{2}:\d{2})$/.test(normalized) ? normalized : `${normalized}Z`;
  const timestamp = Date.parse(utcValue);
  return Number.isFinite(timestamp) && timestamp > Date.now();
}

function resolveCollectionTarget(data: FilterOptions, current: Pick<CollectionJobForm, "accountId" | "groupId">) {
  current = retainExistingTarget(current, data);
  const accountId = current.accountId || (data.accounts[0] ? String(data.accounts[0].id) : "");
  const groupId =
    current.groupId ||
    (accountId ? String(data.groups.find((group) => String(group.account_id) === accountId)?.id ?? "") : "");
  return { accountId, groupId };
}

function resolveApiTargetForm(
  data: FilterOptions,
  accountId: string,
  groupId: string,
): ApiTargetForm {
  const account = data.accounts.find((item) => String(item.id) === accountId);
  const group = data.groups.find((item) => String(item.id) === groupId);
  return {
    accountName: account?.display_name ?? "",
    groupName: group?.name ?? "",
    sourceGroupId: group?.source_group_id ?? "",
  };
}

function formatObservationType(type: string): string {
  const labels: Record<string, string> = {
    message_history: "历史消息",
    pagination: "分页",
    image: "图片",
    file: "文件",
    link: "链接",
    red_packet: "红包",
    error: "错误",
    official_api: "官方接口",
  };
  return labels[type] ?? type;
}

function formatRiskLevel(level: string): string {
  const labels: Record<string, string> = {
    unknown: "未确认",
    low: "低",
    medium: "中",
    high: "高",
  };
  return labels[level] ?? level;
}

function formatVerificationStatus(status: string): string {
  const labels: Record<string, string> = {
    draft: "记录中",
    verified: "已验证",
    blocked: "受阻",
  };
  return labels[status] ?? status;
}

function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatCollectionStatus(status: string): string {
  const labels: Record<string, string> = {
    queued: "排队中",
    awaiting_confirmation: "待确认",
    running: "采集中",
    stopped: "已停止",
    completed: "已完成",
    cancelled: "已取消",
    failed: "失败",
  };
  return labels[status] ?? status;
}

function formatBrowserSessionStatus(status: BrowserLoginSessionStatus): string {
  const labels: Record<BrowserLoginSessionStatus, string> = {
    launching_browser: "正在启动浏览器",
    awaiting_scan: "等待扫码",
    login_detected: "已检测登录，正在保存 Cookie",
    cookie_saved: "Cookie 已保存",
    awaiting_group_selection: "请在浏览器中打开目标群聊",
    group_candidate_found: "已发现候选群 ID",
    binding_confirmed: "群 ID 已确认，正在关闭浏览器",
    completed: "登录与群聊绑定已完成",
    cancelled: "已取消",
    timed_out: "会话已超时",
    failed: "会话失败",
  };
  return labels[status];
}

function parseJsonText(value: string, label: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    throw new Error(`${label} 需要填写合法 JSON，或留空`);
  }
}

function stringifyJsonPreview(value: unknown): string {
  if (value === null || value === undefined) return "";
  return JSON.stringify(value, null, 2);
}

function isInsightView(mode: ViewMode): mode is "dashboard"|"logs"|"ocr" { return ["dashboard","logs","ocr"].includes(mode); }

function viewDescription(mode: ViewMode) {
  const labels: Record<ViewMode,string> = {
    accounts:"维护本地账号资料、启停状态、采集与归档入口", groups:"维护群聊资料、绑定与归档状态",
    dashboard:"查看本地消息趋势、归档情况与原件占用", logs:"查看任务历史、定位失败资源与导出诊断记录",
    ocr:"本地识别图片文字，搜索并定位原消息", jobs:"选择账号、群聊、Cookie 和时间范围，创建全局串行采集任务",
    monitor:"查看每次采集的断点、时间覆盖、过滤计数和停止原因", tools:"导入本地 JSON/CSV 或网页快照，并维护 API 采集目标",
    weibo:"记录微博实机验证结果和脱敏接口观察，为真实采集器做准备", deleted:"回收站：查看、恢复或彻底删除已删除消息",
    active:"按日期分组展示，游标分页加载更早消息"
  }; return labels[mode];
}

function appShellClass(viewMode: ViewMode) {
  return ['active', 'deleted'].includes(viewMode) ? 'app-shell reader-shell' : 'app-shell';
}

export function App() {
  const [managementRevision, setManagementRevision] = useState(0);
  const [externalMessage,setExternalMessage]=useState<MessageRef|undefined>();
  const [viewMode, setViewMode] = useState<ViewMode>("jobs");
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [appliedFilters, setAppliedFilters] = useState<Filters>(emptyFilters);
  const [options, setOptions] = useState<FilterOptions>({
    accounts: [],
    groups: [],
    users: [],
    message_types: [],
  });
  const [collectionJobs, setCollectionJobs] = useState<CollectionJob[]>([]);
  const [collectionJobTotal, setCollectionJobTotal] = useState(0);
  const [collectionJobOffset, setCollectionJobOffset] = useState(0);
  const [collectionJobForm, setCollectionJobForm] = useState<CollectionJobForm>(
    emptyCollectionJobForm,
  );
  const [apiTargetForm, setApiTargetForm] = useState<ApiTargetForm>(emptyApiTargetForm);
  const [cookieFileName, setCookieFileName] = useState("");
  const [browserLoginSession, setBrowserLoginSession] =
    useState<BrowserLoginSession | null>(null);
  const [browserLoginLoading, setBrowserLoginLoading] = useState(false);
  const [collectionJobStatusFilter, setCollectionJobStatusFilter] = useState("");
  const [importFiles, setImportFiles] = useState<ImportFile[]>([]);
  const [browserCaptures, setBrowserCaptures] = useState<BrowserCapture[]>([]);
  const [browserCaptureSnippet, setBrowserCaptureSnippet] = useState("");
  const [browserCapturePreview, setBrowserCapturePreview] =
    useState<BrowserCaptureParsePreview | null>(null);
  const [jobLoading, setJobLoading] = useState(false);
  const [jobActionId, setJobActionId] = useState<number | null>(null);
  const [expandedJobId, setExpandedJobId] = useState<number | null>(null);
  const [attemptLoadingJobId, setAttemptLoadingJobId] = useState<number | null>(null);
  const [jobAttempts, setJobAttempts] = useState<Record<number, CollectionJobAttempt[]>>({});
  const [jobMessage, setJobMessage] = useState<string | null>(null);
  const [verificationReports, setVerificationReports] = useState<WeiboVerificationReport[]>([]);
  const [verificationTotal, setVerificationTotal] = useState(0);
  const [verificationForm, setVerificationForm] = useState<VerificationReportForm>(
    emptyVerificationReportForm,
  );
  const [verificationStatusFilter, setVerificationStatusFilter] = useState("");
  const [selectedVerificationId, setSelectedVerificationId] = useState<number | null>(null);
  const [selectedVerification, setSelectedVerification] =
    useState<WeiboVerificationReport | null>(null);
  const [observationForm, setObservationForm] = useState<ObservationForm>(emptyObservationForm);
  const [verificationLoading, setVerificationLoading] = useState(false);
  const [verificationMessage, setVerificationMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const {messages, selectedMessageId, setSelectedMessageId, detail, setDetail, total, attachmentSummary,
    nextCursor, hasMore, loading, setLoading, loadingMore, detailLoading, loadMessages, loadDetail}
    = useMessageData(appliedFilters, viewMode, setError);

  const visibleCollectionGroups = useMemo(() => {
    if (!collectionJobForm.accountId) return options.groups;
    return options.groups.filter(
      (group) => String(group.account_id) === collectionJobForm.accountId,
    );
  }, [collectionJobForm.accountId, options.groups]);

  const selectedCollectionAccount = useMemo(
    () => options.accounts.find((account) => String(account.id) === collectionJobForm.accountId),
    [collectionJobForm.accountId, options.accounts],
  );

  const selectedCollectionGroup = useMemo(
    () => options.groups.find((group) => String(group.id) === collectionJobForm.groupId),
    [collectionJobForm.groupId, options.groups],
  );

  const browserSessionAccount = useMemo(
    () =>
      options.accounts.find((account) => account.id === browserLoginSession?.account_id) ?? null,
    [browserLoginSession?.account_id, options.accounts],
  );

  const browserSessionGroup = useMemo(
    () => options.groups.find((group) => group.id === browserLoginSession?.group_id) ?? null,
    [browserLoginSession?.group_id, options.groups],
  );

  const browserSessionActive = Boolean(
    browserLoginSession && !terminalBrowserSessionStatuses.has(browserLoginSession.status),
  );

  const visibleVerificationGroups = useMemo(() => {
    if (!verificationForm.accountId) return options.groups;
    return options.groups.filter(
      (group) => String(group.account_id) === verificationForm.accountId,
    );
  }, [verificationForm.accountId, options.groups]);

  async function loadOptions(target: Pick<CollectionJobForm, "accountId" | "groupId"> = collectionJobForm) {
    const [data, apiStatus] = await Promise.all([
      fetchJson<FilterOptions>("/api/filter-options", "筛选项加载"),
      fetchJson<{ accounts: Array<Account & { cookie_profile: CookieProfileStatus }> }>(
        "/api/weibo-api/status",
        "API 状态加载",
      ),
    ]);
    const statusByAccount = new Map(
      apiStatus.accounts.map((account) => [account.id, account.cookie_profile]),
    );
    const mergedData: FilterOptions = {
      ...data,
      accounts: data.accounts.map((account) => ({
        ...account,
        cookie_profile: statusByAccount.get(account.id),
      })),
    };
    setOptions(mergedData);
    setFilters(current => retainExistingFilters(current, mergedData));
    setAppliedFilters(current => retainExistingFilters(current, mergedData));
    setVerificationForm(current => retainExistingTarget(current, mergedData));
    const { accountId, groupId } = resolveCollectionTarget(mergedData, target);
    setCollectionJobForm((current) => ({ ...current, accountId, groupId }));
    setApiTargetForm(resolveApiTargetForm(mergedData, accountId, groupId));
    return mergedData;
  }

  const {actionPreview, deleteResult, deleting, invalidatePreview, previewAction,
    executeSoftDelete, executeRestore, executeHardDelete} = useBatchActions(appliedFilters, viewMode, setError, async () => {
      setSelectedMessageId(null); setDetail(null);
      await Promise.all([loadOptions(), loadMessages(appliedFilters)]);
    });

  async function loadCollectionJobs(
    status = collectionJobStatusFilter,
    quiet = false,
    offset = collectionJobOffset,
  ) {
    if (!quiet) setJobLoading(true);
    if (!quiet) setError(null);
    try {
      const params = new URLSearchParams();
      params.set("limit", String(COLLECTION_JOB_PAGE_SIZE));
      params.set("offset", String(offset));
      if (status) params.set("status", status);
      const data = await fetchJson<{
        items: CollectionJob[];
        total: number;
      }>(`/api/collection-jobs?${params.toString()}`, "采集任务加载");
      setCollectionJobs(data.items);
      setCollectionJobTotal(data.total);
      setCollectionJobOffset(offset);
      if (expandedJobId !== null) {
        const attempts = await fetchJson<{ items: CollectionJobAttempt[] }>(
          `/api/collection-jobs/${expandedJobId}/attempts`,
          "采集运行记录刷新",
        );
        setJobAttempts((current) => ({ ...current, [expandedJobId]: attempts.items }));
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      if (!quiet) setJobLoading(false);
    }
  }

  async function toggleCollectionJobAttempts(jobId: number) {
    if (expandedJobId === jobId) {
      setExpandedJobId(null);
      return;
    }
    setExpandedJobId(jobId);
    if (jobAttempts[jobId]) return;

    setAttemptLoadingJobId(jobId);
    try {
      const data = await fetchJson<{ items: CollectionJobAttempt[] }>(
        `/api/collection-jobs/${jobId}/attempts`,
        "采集运行记录加载",
      );
      setJobAttempts((current) => ({ ...current, [jobId]: data.items }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "运行记录加载失败");
    } finally {
      setAttemptLoadingJobId(null);
    }
  }

  async function loadImportFiles() {
    setError(null);
    try {
      const data = await fetchJson<{
        items: ImportFile[];
        total: number;
      }>("/api/import-files", "采集文件列表加载");
      setImportFiles(data.items);
      setCollectionJobForm((current) => {
        if (current.sourceFile || data.items.length === 0) return current;
        return { ...current, sourceFile: data.items[0].relative_path };
      });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    }
  }

  async function loadBrowserCaptures(
    accountId = collectionJobForm.accountId,
    groupId = collectionJobForm.groupId,
  ) {
    setError(null);
    try {
      const params = new URLSearchParams();
      if (accountId) params.set("account_id", accountId);
      if (groupId) params.set("group_id", groupId);
      const query = params.toString();
      const data = await fetchJson<{
        items: BrowserCapture[];
        total: number;
      }>(`/api/browser-captures${query ? `?${query}` : ""}`, "网页快照列表加载");
      setBrowserCaptures(data.items);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? `网页快照读取失败：${requestError.message}`
          : "网页快照读取失败",
      );
    }
  }

  async function generateBrowserCaptureSnippet() {
    setJobLoading(true);
    setJobMessage(null);
    setError(null);
    try {
      if (
        !collectionJobForm.accountId ||
        !collectionJobForm.groupId ||
        !collectionJobForm.rangeStart ||
        !collectionJobForm.rangeEnd
      ) {
        throw new Error("请先选择账号、群聊、开始时间和结束时间");
      }
      const params = new URLSearchParams({
        account_id: collectionJobForm.accountId,
        group_id: collectionJobForm.groupId,
        range_start: formatDateTimeLocal(collectionJobForm.rangeStart),
        range_end: formatDateTimeLocal(collectionJobForm.rangeEnd),
      });
      const response = await fetch(`${API_BASE_URL}/api/browser-capture/snippet?${params.toString()}`);
      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(errorBody?.detail || "网页快照脚本生成失败");
      }
      const data = (await response.json()) as { script: string };
      setBrowserCaptureSnippet(data.script);
      setJobMessage("网页快照脚本已生成");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setJobLoading(false);
    }
  }

  async function copyBrowserCaptureSnippet() {
    if (!browserCaptureSnippet) return;
    await navigator.clipboard.writeText(browserCaptureSnippet);
    setJobMessage("网页快照脚本已复制");
  }

  async function previewBrowserCapture(captureId: number) {
    setJobLoading(true);
    setJobMessage(null);
    setError(null);
    try {
      const data = await fetchJson<BrowserCaptureParsePreview>(
        `/api/browser-captures/${captureId}/parse-preview`,
        "网页快照解析预览",
      );
      setBrowserCapturePreview(data);
      setJobMessage(
        `快照 #${captureId} 解析出 ${data.parsed_count} 条消息，重复 ${data.duplicate_count} 条`,
      );
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setJobLoading(false);
    }
  }

  async function importBrowserCapture() {
    if (!browserCapturePreview) return;
    const confirmed = window.confirm(
      `确认将快照 #${browserCapturePreview.capture_id} 中解析出的 ${browserCapturePreview.parsed_count} 条消息写入数据库？`,
    );
    if (!confirmed) return;

    setJobLoading(true);
    setJobMessage(null);
    setError(null);
    try {
      const result = await fetchJson<BrowserCaptureImportResult>(
        `/api/browser-captures/${browserCapturePreview.capture_id}/import`,
        "网页快照入库",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: true }),
        },
      );
      setJobMessage(
        `已入库 ${result.inserted_count} 条，跳过 ${result.skipped_count} 条，重复 ${result.duplicate_count} 条，红包 ${result.red_packet_count} 条，系统通知 ${result.filtered_system_notice_count} 条`,
      );
      setBrowserCapturePreview(null);
      await loadBrowserCaptures();
      await loadCollectionJobs();
      await loadMessages(appliedFilters);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setJobLoading(false);
    }
  }

  async function saveApiTarget() {
    setJobLoading(true);
    setJobMessage(null);
    setError(null);
    try {
      if (
        !apiTargetForm.accountName.trim() ||
        !apiTargetForm.groupName.trim() ||
        !apiTargetForm.sourceGroupId.trim()
      ) {
        throw new Error("请填写账号名称、群聊名称和群 ID");
      }
      const response = await fetch(`${API_BASE_URL}/api/weibo-api/targets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: collectionJobForm.accountId
            ? Number(collectionJobForm.accountId)
            : null,
          account_name: apiTargetForm.accountName.trim(),
          group_id: collectionJobForm.groupId ? Number(collectionJobForm.groupId) : null,
          group_name: apiTargetForm.groupName.trim(),
          source_group_id: apiTargetForm.sourceGroupId.trim(),
        }),
      });
      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(errorBody?.detail || "API 采集目标保存失败");
      }
      const result = (await response.json()) as { account: Account; group: Group };
      await loadOptions();
      setCollectionJobForm((current) => ({
        ...current,
        accountId: String(result.account.id),
        groupId: String(result.group.id),
      }));
      setApiTargetForm({
        accountName: result.account.display_name,
        groupName: result.group.name,
        sourceGroupId: result.group.source_group_id ?? "",
      });
      setJobMessage("API 账号和群聊绑定已保存");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setJobLoading(false);
    }
  }

  async function importAccountCookies(file: File | undefined) {
    if (!file) return;
    setJobLoading(true);
    setJobMessage(null);
    setError(null);
    try {
      if (!collectionJobForm.accountId) {
        throw new Error("请先保存或选择一个账号");
      }
      const parsed = JSON.parse(await file.text()) as unknown;
      const cookies = Array.isArray(parsed)
        ? parsed
        : typeof parsed === "object" && parsed !== null && "cookies" in parsed
          ? (parsed as { cookies: unknown }).cookies
          : null;
      if (!Array.isArray(cookies)) {
        throw new Error("Cookie 文件必须是 Puppeteer 导出的 JSON 数组");
      }
      const response = await fetch(
        `${API_BASE_URL}/api/weibo-api/accounts/${collectionJobForm.accountId}/cookies`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ cookies }),
        },
      );
      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(errorBody?.detail || "账号 Cookie 导入失败");
      }
      const status = (await response.json()) as { cookie_count?: number };
      setCookieFileName(file.name);
      await loadOptions();
      setJobMessage(`账号登录态已安全导入（${status.cookie_count ?? cookies.length} 个 Cookie）`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Cookie 文件读取失败");
    } finally {
      setJobLoading(false);
    }
  }

  async function loadActiveBrowserLoginSession() {
    const data = await fetchJson<{ session: BrowserLoginSession | null }>(
      "/api/weibo-api/browser-sessions/active",
      "浏览器登录会话读取",
    );
    setBrowserLoginSession(data.session);
    if (data.session?.cookie_ready) await loadOptions();
  }

  async function startBrowserLoginSession() {
    setBrowserLoginLoading(true);
    setJobMessage(null);
    setError(null);
    try {
      if (!collectionJobForm.accountId) throw new Error("请先选择账号");
      const session = await fetchJson<BrowserLoginSession>(
        "/api/weibo-api/browser-sessions",
        "扫码登录启动",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            account_id: Number(collectionJobForm.accountId),
            group_id: collectionJobForm.groupId ? Number(collectionJobForm.groupId) : null,
          }),
        },
      );
      setBrowserLoginSession(session);
      setJobMessage("扫码窗口正在打开；请使用当前所选微博账号扫码");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "扫码登录启动失败");
    } finally {
      setBrowserLoginLoading(false);
    }
  }

  async function cancelBrowserLoginSession() {
    if (!browserLoginSession) return;
    setBrowserLoginLoading(true);
    setError(null);
    try {
      const session = await fetchJson<BrowserLoginSession>(
        `/api/weibo-api/browser-sessions/${browserLoginSession.session_id}/cancel`,
        "浏览器登录取消",
        { method: "POST" },
      );
      setBrowserLoginSession(session);
      setJobMessage("浏览器登录会话已取消；已保存的有效 Cookie 会继续保留");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "取消失败");
    } finally {
      setBrowserLoginLoading(false);
    }
  }

  async function rediscoverBrowserGroup() {
    if (!browserLoginSession) return;
    setBrowserLoginLoading(true);
    setError(null);
    try {
      const session = await fetchJson<BrowserLoginSession>(
        `/api/weibo-api/browser-sessions/${browserLoginSession.session_id}/rediscover`,
        "群 ID 重新发现",
        { method: "POST" },
      );
      setBrowserLoginSession(session);
      setJobMessage("请在扫码窗口中重新打开当前目标群聊");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "重新发现失败");
    } finally {
      setBrowserLoginLoading(false);
    }
  }

  async function confirmBrowserGroup() {
    if (!browserLoginSession?.candidate_source_group_id || !browserSessionGroup) return;
    const confirmed = window.confirm(
      `确认把候选群 ID ${browserLoginSession.candidate_source_group_id} 绑定到“${browserSessionGroup.name}”？`,
    );
    if (!confirmed) return;
    setBrowserLoginLoading(true);
    setError(null);
    try {
      const session = await fetchJson<BrowserLoginSession>(
        `/api/weibo-api/browser-sessions/${browserLoginSession.session_id}/confirm-group`,
        "群 ID 绑定",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            candidate_source_group_id: browserLoginSession.candidate_source_group_id,
          }),
        },
      );
      setBrowserLoginSession(session);
      await loadOptions();
      setJobMessage(`群 ID 已绑定到“${browserSessionGroup.name}”`);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "群 ID 绑定失败");
    } finally {
      setBrowserLoginLoading(false);
    }
  }

  async function createWeiboApiJob() {
    setJobLoading(true);
    setJobMessage(null);
    setError(null);
    try {
      if (
        !collectionJobForm.accountId ||
        !collectionJobForm.groupId ||
        !collectionJobForm.rangeStart ||
        !collectionJobForm.rangeEnd
      ) {
        throw new Error("请先选择账号、群聊、开始时间和结束时间");
      }
      if (!selectedCollectionAccount?.cookie_profile?.authenticated) {
        throw new Error("当前账号 Cookie 未就绪，请先扫码登录或使用备用文件导入");
      }
      if (!selectedCollectionGroup?.source_group_id) {
        throw new Error("当前群聊尚未绑定群 ID，请先完成浏览器发现并确认绑定");
      }
      const response = await fetch(`${API_BASE_URL}/api/collection-jobs/weibo-api`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: Number(collectionJobForm.accountId),
          group_id: Number(collectionJobForm.groupId),
          range_start: formatDateTimeLocal(collectionJobForm.rangeStart),
          range_end: formatDateTimeLocal(collectionJobForm.rangeEnd),
        }),
      });
      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: unknown } | null;
        const detail =
          typeof errorBody?.detail === "string"
            ? errorBody.detail
            : "采集任务创建失败";
        throw new Error(detail);
      }
      const created = (await response.json()) as CollectionJob;
      setJobMessage(`采集任务 #${created.id} 已进入全局队列`);
      setCollectionJobStatusFilter("");
      await loadCollectionJobs("", false, 0);
      setViewMode("monitor");
    } catch (requestError) {
      await loadCollectionJobs(collectionJobStatusFilter, true, collectionJobOffset).catch(
        () => undefined,
      );
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setJobLoading(false);
    }
  }

  async function runCollectionJobAction(
    job: CollectionJob,
    action: "confirm" | "resume" | "stop",
  ) {
    if (
      action === "confirm" &&
      !window.confirm("请先关闭微博 App 和所有微博网页。确认已经关闭后再启动采集？")
    ) {
      return;
    }
    if (
      action === "stop" &&
      !window.confirm(
        job.status === "running"
          ? "安全停止会等待当前请求和当前页提交完成，确认停止？"
          : "确认将这个任务移出等待队列？之后仍可沿原断点重新排队。",
      )
    ) {
      return;
    }

    setJobActionId(job.id);
    setJobMessage(null);
    setError(null);
    try {
      const actionLabel =
        action === "confirm" ? "确认启动" : action === "resume" ? "续传" : "安全停止";
      await fetchJson<CollectionJob>(`/api/collection-jobs/${job.id}/${action}`, actionLabel, {
        method: "POST",
      });
      setJobMessage(
        action === "confirm"
          ? `任务 #${job.id} 已确认启动`
          : action === "resume"
            ? `任务 #${job.id} 已沿断点重新排队`
            : `任务 #${job.id} 已提交安全停止请求`,
      );
      setJobAttempts((current) => {
        const next = { ...current };
        delete next[job.id];
        return next;
      });
      await loadCollectionJobs(collectionJobStatusFilter, true, collectionJobOffset);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "任务操作失败");
    } finally {
      setJobActionId(null);
    }
  }

  async function createCollectionJob() {
    setJobLoading(true);
    setJobMessage(null);
    setError(null);
    try {
      if (
        !collectionJobForm.accountId ||
        !collectionJobForm.groupId ||
        !collectionJobForm.rangeStart ||
        !collectionJobForm.rangeEnd
      ) {
        throw new Error("请先选择账号、群聊、开始时间和结束时间");
      }
      const response = await fetch(`${API_BASE_URL}/api/collection-jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: Number(collectionJobForm.accountId),
          group_id: Number(collectionJobForm.groupId),
          range_start: formatDateTimeLocal(collectionJobForm.rangeStart),
          range_end: formatDateTimeLocal(collectionJobForm.rangeEnd),
          collector_type: "time_range_placeholder",
        }),
      });
      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(errorBody?.detail || "采集任务创建失败");
      }
      const created = (await response.json()) as CollectionJob;
      setJobMessage(`已创建采集任务 #${created.id}`);
      await loadCollectionJobs();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setJobLoading(false);
    }
  }

  async function runSingleGroupFileCollection() {
    setJobLoading(true);
    setJobMessage(null);
    setError(null);
    try {
      if (
        !collectionJobForm.accountId ||
        !collectionJobForm.groupId ||
        !collectionJobForm.rangeStart ||
        !collectionJobForm.rangeEnd ||
        !collectionJobForm.sourceFile
      ) {
        throw new Error("请先选择账号、群聊、开始时间、结束时间和采集文件");
      }
      const response = await fetch(`${API_BASE_URL}/api/collection-jobs/single-group-file`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: Number(collectionJobForm.accountId),
          group_id: Number(collectionJobForm.groupId),
          range_start: formatDateTimeLocal(collectionJobForm.rangeStart),
          range_end: formatDateTimeLocal(collectionJobForm.rangeEnd),
          source_file: collectionJobForm.sourceFile,
          copy_local_attachments: true,
        }),
      });
      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(errorBody?.detail || "单群聊文件采集失败");
      }
      const result = (await response.json()) as {
        summary: SingleGroupCollectionSummary;
        job: CollectionJob;
      };
      setJobMessage(
        `已完成采集 #${result.summary.collection_job_id}：新增 ${result.summary.inserted_count}，跳过 ${result.summary.skipped_count}，红包 ${result.summary.red_packet_count}，系统通知 ${result.summary.filtered_system_notice_count}，附件 ${result.summary.attachment_count}`,
      );
      await loadCollectionJobs();
      await loadOptions();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setJobLoading(false);
    }
  }

  async function loadVerificationReports(status = verificationStatusFilter) {
    setVerificationLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      params.set("limit", "50");
      if (status) params.set("verification_status", status);
      const response = await fetch(`${API_BASE_URL}/api/weibo-verifications?${params.toString()}`);
      if (!response.ok) throw new Error("微博验证记录加载失败");
      const data = (await response.json()) as {
        items: WeiboVerificationReport[];
        total: number;
      };
      setVerificationReports(data.items);
      setVerificationTotal(data.total);
      if (
        selectedVerificationId !== null &&
        !data.items.some((report) => report.id === selectedVerificationId)
      ) {
        setSelectedVerificationId(null);
        setSelectedVerification(null);
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setVerificationLoading(false);
    }
  }

  async function loadVerificationDetail(reportId: number) {
    setVerificationLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE_URL}/api/weibo-verifications/${reportId}`);
      if (!response.ok) throw new Error("微博验证详情加载失败");
      setSelectedVerification((await response.json()) as WeiboVerificationReport);
      setSelectedVerificationId(reportId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setVerificationLoading(false);
    }
  }

  async function createVerificationReport() {
    setVerificationLoading(true);
    setVerificationMessage(null);
    setError(null);
    try {
      if (!verificationForm.accountId || !verificationForm.groupId) {
        throw new Error("请先选择账号和群聊");
      }
      const response = await fetch(`${API_BASE_URL}/api/weibo-verifications`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          account_id: Number(verificationForm.accountId),
          group_id: Number(verificationForm.groupId),
          history_days_checked: verificationForm.historyDaysChecked
            ? Number(verificationForm.historyDaysChecked)
            : null,
          risk_level: verificationForm.riskLevel,
          verification_status: verificationForm.verificationStatus,
          notes: verificationForm.notes.trim() || null,
        }),
      });
      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(errorBody?.detail || "微博验证记录创建失败");
      }
      const created = (await response.json()) as WeiboVerificationReport;
      setVerificationMessage(`已创建微博验证记录 #${created.id}`);
      setSelectedVerification(created);
      setSelectedVerificationId(created.id);
      await loadVerificationReports();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setVerificationLoading(false);
    }
  }

  function updateSelectedVerification<K extends keyof WeiboVerificationReport>(
    key: K,
    value: WeiboVerificationReport[K],
  ) {
    setSelectedVerification((current) => (current ? { ...current, [key]: value } : current));
  }

  async function saveVerificationReport() {
    if (!selectedVerification) return;
    setVerificationLoading(true);
    setVerificationMessage(null);
    setError(null);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/weibo-verifications/${selectedVerification.id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            can_login: selectedVerification.can_login,
            can_view_group: selectedVerification.can_view_group,
            can_view_history: selectedVerification.can_view_history,
            history_days_checked: selectedVerification.history_days_checked,
            can_page_history: selectedVerification.can_page_history,
            can_access_images: selectedVerification.can_access_images,
            can_access_files: selectedVerification.can_access_files,
            can_access_links: selectedVerification.can_access_links,
            red_packet_identified: selectedVerification.red_packet_identified,
            rate_limit_observed: selectedVerification.rate_limit_observed,
            risk_level: selectedVerification.risk_level,
            verification_status: selectedVerification.verification_status,
            notes: selectedVerification.notes,
          }),
        },
      );
      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(errorBody?.detail || "微博验证记录保存失败");
      }
      setSelectedVerification((await response.json()) as WeiboVerificationReport);
      setVerificationMessage("微博验证记录已保存");
      await loadVerificationReports();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setVerificationLoading(false);
    }
  }

  async function createObservation() {
    if (!selectedVerification) return;
    setVerificationLoading(true);
    setVerificationMessage(null);
    setError(null);
    try {
      const response = await fetch(
        `${API_BASE_URL}/api/weibo-verifications/${selectedVerification.id}/observations`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            observation_type: observationForm.observationType,
            method: observationForm.method.trim() || null,
            endpoint_path: observationForm.endpointPath.trim() || null,
            request_fields: parseJsonText(observationForm.requestFields, "请求字段"),
            response_fields: parseJsonText(observationForm.responseFields, "响应字段"),
            sample_payload: parseJsonText(observationForm.samplePayload, "样例内容"),
            pagination_fields: parseJsonText(observationForm.paginationFields, "分页字段"),
            redaction_notes: observationForm.redactionNotes.trim() || null,
          }),
        },
      );
      if (!response.ok) {
        const errorBody = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(errorBody?.detail || "接口观察记录创建失败");
      }
      setObservationForm(emptyObservationForm);
      setVerificationMessage("已添加脱敏观察记录");
      await loadVerificationDetail(selectedVerification.id);
      await loadVerificationReports();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "请求失败");
    } finally {
      setVerificationLoading(false);
    }
  }

  async function loadJobView(target: Pick<CollectionJobForm, "accountId" | "groupId"> = collectionJobForm) {
    setError(null);
    await Promise.all([loadOptions(target), loadCollectionJobs(), loadActiveBrowserLoginSession()]);
  }

  async function loadAdvancedToolsView() {
    setError(null);
    await Promise.all([loadOptions(), loadImportFiles(), loadBrowserCaptures()]);
  }

  async function loadMessageView(nextViewMode: ViewMode = viewMode) {
    setError(null);
    await Promise.all([
      loadOptions(),
      loadMessages(appliedFilters, "replace", null, nextViewMode),
    ]);
  }

  useEffect(() => {
    async function bootstrap() {
      setLoading(true);
      try {
        await loadJobView();
      } catch (requestError) {
        setError(
          requestError instanceof Error
            ? requestError.message
            : "无法连接后端，请确认 127.0.0.1:8000 已启动",
        );
      } finally {
        setLoading(false);
      }
    }

    void bootstrap();
  }, []);

  useEffect(() => {
    if (selectedMessageId !== null) {
      void loadDetail(selectedMessageId);
    }
  }, [selectedMessageId]);

  useEffect(() => {
    if (viewMode !== "monitor") return undefined;
    const timer = window.setInterval(() => {
      void loadCollectionJobs(collectionJobStatusFilter, true, collectionJobOffset);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [viewMode, collectionJobStatusFilter, collectionJobOffset, expandedJobId]);

  useBrowserLoginPolling({
    session: browserLoginSession,
    onSession: setBrowserLoginSession,
    onCookieReady: loadOptions,
    onError: setError,
  });

  function updateFilter<K extends keyof Filters>(key: K, value: Filters[K]) {
    setFilters((current) => {
      const next = { ...current, [key]: value };
      if (key === "accountId") next.groupId = "";
      if (key === "accountId" || key === "groupId") next.viewer = "";
      return next;
    });
    invalidatePreview();
  }

  function applySearch(nextFilters = filters) {
    nextFilters = normalizeDateHours(nextFilters);
    setFilters(nextFilters);
    setAppliedFilters(nextFilters);
    setSelectedMessageId(null);
    invalidatePreview();
    void loadMessages(nextFilters);
  }

  function resetSearch() {
    setFilters(emptyFilters);
    setAppliedFilters(emptyFilters);
    setSelectedMessageId(null);
    invalidatePreview();
    void loadMessages(emptyFilters);
  }

  function loadMore() {
    if (!nextCursor || loadingMore) return;
    void loadMessages(appliedFilters, "append", nextCursor);
  }

  function switchView(nextViewMode: ViewMode, target: Pick<CollectionJobForm, "accountId" | "groupId"> = collectionJobForm) {
    setViewMode(nextViewMode); setExternalMessage(undefined);
    setError(null);
    setSelectedMessageId(null);
    setDetail(null);
    invalidatePreview();
    setJobMessage(null);
    setVerificationMessage(null);
    if (["accounts", "groups", "dashboard", "logs", "ocr"].includes(nextViewMode)) return;
    if (nextViewMode === "jobs") {
      void loadJobView(target).catch((requestError) => {
        setError(requestError instanceof Error ? requestError.message : "请求失败");
      });
    } else if (nextViewMode === "monitor") {
      void loadCollectionJobs();
    } else if (nextViewMode === "tools") {
      void loadAdvancedToolsView().catch((requestError) => {
        setError(requestError instanceof Error ? requestError.message : "请求失败");
      });
    } else if (nextViewMode === "weibo") {
      void loadVerificationReports();
    } else {
      void loadMessageView(nextViewMode).catch((requestError) => {
        setError(requestError instanceof Error ? requestError.message : "请求失败");
      });
    }
  }

  function navigateTarget(view: "active" | "jobs" | "monitor", accountId: number, groupId?: number) {
    const target = {accountId: String(accountId), groupId: groupId ? String(groupId) : ""};
    setCollectionJobForm(current => ({...current, ...target}));
    if (view === "active") {
      setViewMode("active"); setSelectedMessageId(null); setDetail(null);
      const next = {...emptyFilters, accountId: String(accountId), groupId: groupId ? String(groupId) : ""};
      setFilters(next); setAppliedFilters(next); invalidatePreview();
      void loadOptions(target); void loadMessages(next, "replace", null, "active");
    } else switchView(view, target);
  }

  const openInsightMessages: OpenMessages = (patch,target,deleted=false) => {
    const next={...emptyFilters,...patch}; const mode=deleted?"deleted":"active";
    setFilters(next);setAppliedFilters(next);setExternalMessage(target);setSelectedMessageId(null);setDetail(null);
    invalidatePreview();setViewMode(mode);
    void loadMessages(next,"replace",null,mode);
  };

  return (
    <main className={appShellClass(viewMode)}>
      <header className="app-header">
        <div>
          <h1>微博群聊归档库</h1>
          <p>
            {viewDescription(viewMode)}
          </p>
        </div>
        <div className="header-actions">
          <ViewNavigation value={viewMode} onChange={switchView} />
          <button
            className="secondary-button"
            type="button"
            onClick={() =>
              ["accounts", "groups", "dashboard", "logs", "ocr"].includes(viewMode)
                ? setManagementRevision(value => value + 1)
                : viewMode === "jobs"
                ? void loadJobView()
                : viewMode === "monitor"
                  ? void loadCollectionJobs()
                : viewMode === "tools"
                  ? void loadAdvancedToolsView()
                : viewMode === "weibo"
                  ? void loadVerificationReports()
                  : void loadMessageView().catch((requestError) => {
                      setError(requestError instanceof Error ? requestError.message : "请求失败");
                    })
            }
          >
            <RefreshCw size={17} aria-hidden="true" />
            刷新
          </button>
        </div>
      </header>

      {isInsightView(viewMode) && <InsightsPage key={`${viewMode}:${managementRevision}`} mode={viewMode} options={options} openMessages={openInsightMessages}/>}
      {(viewMode === "accounts" || viewMode === "groups") && <ManagementPage key={`${viewMode}:${managementRevision}`} mode={viewMode} onNavigate={navigateTarget} onChanged={loadOptions} />}
      {!["accounts", "groups", "dashboard", "logs", "ocr"].includes(viewMode) && <>
      <section className="status-row">
        <article>
          {viewMode === "jobs" || viewMode === "monitor" ? (
            <Clock size={18} aria-hidden="true" />
          ) : viewMode === "tools" ? (
            <Database size={18} aria-hidden="true" />
          ) : viewMode === "weibo" ? (
            <ShieldCheck size={18} aria-hidden="true" />
          ) : (
            <Database size={18} aria-hidden="true" />
          )}
          <span>
            {viewMode === "jobs"
              ? `${options.accounts.length} 个采集账号`
              : viewMode === "monitor"
                ? `${collectionJobTotal} 个任务`
                : viewMode === "tools"
                  ? `${importFiles.length} 个待导入文件`
                  : viewMode === "weibo"
                    ? `${verificationTotal} 条验证记录`
                    : `${total} 条消息`}
          </span>
        </article>
        <article>
          <CalendarDays size={18} aria-hidden="true" />
          <span>
            {viewMode === "jobs"
              ? "新任务从 max_mid=0 开始"
              : viewMode === "monitor"
                ? "每 5 秒刷新任务进度"
                : viewMode === "tools"
                  ? "本地导入不占微博 API 队列"
                  : viewMode === "weibo"
                    ? "先记录脱敏结构，不保存 Cookie 或 Token"
                    : viewMode === "deleted"
                      ? "当前只看已删除消息"
                      : `每次加载 ${PAGE_SIZE} 条`}
          </span>
        </article>
        <article>
          <Search size={18} aria-hidden="true" />
          <span>
            {viewMode === "jobs"
              ? "轮到任务后需确认已关闭微博 App / 网页"
              : viewMode === "monitor"
                ? "停止任务可沿已提交断点续传"
                : viewMode === "tools"
                  ? "JSON、CSV 与网页快照统一从这里导入"
                  : viewMode === "weibo"
                    ? "观察样例必须先脱敏"
                    : "正文与附件字段可搜索"}
          </span>
        </article>
      </section>
      </>}

      {error ? <div className="alert">{error}</div> : null}
      {deleteResult ? <div className="success-alert">{deleteResult}</div> : null}
      {jobMessage ? <div className="success-alert">{jobMessage}</div> : null}
      {verificationMessage ? <div className="success-alert">{verificationMessage}</div> : null}

      {viewMode === "jobs" ? (
        <section className="collection-create-workspace">
          <aside className="job-form-panel">
            <div className="panel-title">
              <h2>创建采集任务</h2>
            </div>

            <label>
              <span>账号</span>
              <select
                value={collectionJobForm.accountId}
                onChange={(event) => {
                  const accountId = event.target.value;
                  const groupId = String(
                    options.groups.find((group) => String(group.account_id) === accountId)?.id ?? "",
                  );
                  setCollectionJobForm((current) => ({
                    ...current,
                    accountId,
                    groupId,
                  }));
                  setApiTargetForm(resolveApiTargetForm(options, accountId, groupId));
                  setCookieFileName("");
                }}
              >
                <option value="">选择账号</option>
                {options.accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.display_name}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span>群聊</span>
              <select
                value={collectionJobForm.groupId}
                onChange={(event) => {
                  const groupId = event.target.value;
                  setCollectionJobForm((current) => ({
                    ...current,
                    groupId,
                  }));
                  setApiTargetForm(
                    resolveApiTargetForm(options, collectionJobForm.accountId, groupId),
                  );
                }}
              >
                <option value="">选择群聊</option>
                {visibleCollectionGroups.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.name}
                  </option>
                ))}
              </select>
            </label>

            <GroupLimitNotice data={options} accountId={collectionJobForm.accountId}/>
            <section className="browser-login-card">
              <div className="browser-login-heading">
                <div>
                  <h3>账号登录与群聊绑定</h3>
                  <p>打开独立 Chrome 窗口扫码，并自动发现目标群聊 ID。</p>
                </div>
                <ShieldCheck size={20} aria-hidden="true" />
              </div>

              {browserLoginSession ? (
                <div className="browser-login-status">
                  <strong>{formatBrowserSessionStatus(browserLoginSession.status)}</strong>
                  <span>
                    会话账号：{browserSessionAccount?.display_name ?? `#${browserLoginSession.account_id}`}
                  </span>
                  <span>
                    目标群聊：{browserSessionGroup?.name ?? "启动时未选择群聊"}
                  </span>
                  {browserLoginSession.candidate_source_group_id ? (
                    <div className="group-candidate">
                      <span>候选群 ID</span>
                      <strong>{browserLoginSession.candidate_source_group_id}</strong>
                      <small>
                        {browserLoginSession.candidate_captured_at
                          ? `发现时间：${browserLoginSession.candidate_captured_at}`
                          : "等待确认"}
                      </small>
                    </div>
                  ) : null}
                  {browserLoginSession.error_message ? (
                    <p className="browser-login-error">{browserLoginSession.error_message}</p>
                  ) : null}
                </div>
              ) : (
                <p className="browser-login-idle">
                  扫码必须使用当前所选账号；登录后请在同一窗口打开当前目标群聊。
                </p>
              )}

              <div className="browser-login-actions">
                {!browserSessionActive ? (
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={startBrowserLoginSession}
                    disabled={browserLoginLoading || !collectionJobForm.accountId}
                  >
                    <LogIn size={16} aria-hidden="true" />
                    扫码登录并发现群 ID
                  </button>
                ) : null}
                {browserLoginSession?.status === "group_candidate_found" ? (
                  <button
                    className="icon-button"
                    type="button"
                    onClick={confirmBrowserGroup}
                    disabled={bindingDisabled(options,browserSessionGroup?.account_id,browserSessionGroup?.source_group_id,browserLoginSession.candidate_source_group_id,browserLoginLoading)}
                  >
                    确认绑定
                  </button>
                ) : null}
                {browserSessionActive &&
                browserLoginSession?.cookie_ready &&
                browserLoginSession.status !== "binding_confirmed" ? (
                  <button
                    className="secondary-button"
                    type="button"
                    onClick={rediscoverBrowserGroup}
                    disabled={browserLoginLoading}
                  >
                    <RefreshCw size={16} aria-hidden="true" />
                    重新发现
                  </button>
                ) : null}
                {browserSessionActive && browserLoginSession?.status !== "binding_confirmed" ? (
                  <button
                    className="danger-outline-button compact-browser-button"
                    type="button"
                    onClick={cancelBrowserLoginSession}
                    disabled={browserLoginLoading}
                  >
                    取消并关闭浏览器
                  </button>
                ) : null}
              </div>

              <p className="browser-login-privacy">
                Cookie 只由后端保存到本机，不会返回页面。候选群 ID 只有点击“确认绑定”后才会写入数据库。
                扫码和发现期间请勿同时操作微博 App 或其他微博网页。
              </p>
            </section>

            <div className="api-readiness form-readiness">
              <span
                className={
                  selectedCollectionAccount?.cookie_profile?.authenticated ? "ready" : "missing"
                }
              >
                Cookie{selectedCollectionAccount?.cookie_profile?.authenticated ? " 已就绪" : " 未就绪"}
              </span>
              <span className={selectedCollectionGroup?.source_group_id ? "ready" : "missing"}>
                群 ID {selectedCollectionGroup?.source_group_id ? "已绑定" : "未绑定"}
              </span>
            </div>

            <CollectionDateTime label="开始时间" value={collectionJobForm.rangeStart}
                onChange={value => setCollectionJobForm(current => ({ ...current, rangeStart: value }))} />

            <CollectionDateTime label="结束时间" value={collectionJobForm.rangeEnd}
                onChange={value => setCollectionJobForm(current => ({ ...current, rangeEnd: value }))} />

            <button
              className="icon-button full-width-button"
              type="button"
              onClick={createWeiboApiJob}
              disabled={
                jobLoading ||
                !selectedCollectionAccount?.cookie_profile?.authenticated ||
                !selectedCollectionGroup?.source_group_id
              }
            >
              <Plus size={16} aria-hidden="true" />
              {jobLoading ? "正在创建…" : "启动采集"}
            </button>

            <p className="form-hint">
              Cookie 仅保存在本机并按账号隔离。
              任务创建后先进入全局队列，不会在当前请求里同步采集。
              {!selectedCollectionAccount?.cookie_profile?.authenticated
                ? " 当前账号 Cookie 未就绪。"
                : !selectedCollectionGroup?.source_group_id
                  ? " 当前群聊 ID 未绑定。"
                  : ""}
            </p>
          </aside>

          <section className="job-list-panel collection-guide">
            <div className="panel-title">
              <h2>启动前说明</h2>
            </div>
            <div className="guide-body">
              <div className="risk-notice">
                <strong>轮到任务后不会立刻请求微博</strong>
                <p>
                  任务会进入“待确认”状态。请关闭微博 App 和所有微博网页，
                  再到采集监控中确认启动。
                </p>
              </div>
              <ol>
                <li>所有账号共用一个微博 API 队列，全局串行采集。</li>
                <li>每页成功提交消息和断点后，才会请求下一页。</li>
                <li>任何错误都会立即停止，不自动重试，也不会推进失败页的断点。</li>
                <li>已停止任务可在采集监控中沿原断点人工续传。</li>
              </ol>
              <button className="secondary-button" type="button" onClick={() => switchView("monitor")}>
                查看采集监控
              </button>
            </div>
          </section>
        </section>
      ) : viewMode === "monitor" ? (
        <section className="job-list-panel monitor-workspace">
          <div className="panel-title">
            <h2>采集监控</h2>
            <span>{jobLoading ? "加载中" : `${collectionJobs.length} / ${collectionJobTotal}`}</span>
          </div>

          <div className="job-toolbar monitor-toolbar">
            <select
              value={collectionJobStatusFilter}
              onChange={(event) => {
                setCollectionJobStatusFilter(event.target.value);
                void loadCollectionJobs(event.target.value, false, 0);
              }}
            >
              <option value="">全部状态</option>
              <option value="queued">排队中</option>
              <option value="awaiting_confirmation">待确认</option>
              <option value="running">采集中</option>
              <option value="stopped">已停止</option>
              <option value="completed">已完成</option>
              <option value="cancelled">已取消</option>
              <option value="failed">失败</option>
            </select>
            <span>运行中自动每 5 秒刷新；进度以页数和当前最早消息时间为准。</span>
          </div>

          <div className="job-table-wrap">
            <table className="job-table">
              <thead>
                <tr>
                  <th>任务</th>
                  <th>采集范围</th>
                  <th>状态</th>
                  <th>时间覆盖进度</th>
                  <th>计数</th>
                  <th>停止原因</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {collectionJobs.map((job) => {
                  const stopReason = job.stop_reason || job.error_message;
                  const cooldownActive = isFutureUtcTimestamp(job.resume_not_before);
                  const safeStopPending = job.status === "running" && Boolean(job.stop_requested_at);
                  const attempts = jobAttempts[job.id] ?? [];
                  return (
                    <React.Fragment key={job.id}>
                    <tr>
                      <td>
                        <strong>#{job.id} · {job.group_name}</strong>
                        <span>{job.account_name}</span>
                        <small>运行 {job.attempt_count ?? 0} 次</small>
                      </td>
                      <td>
                        <span>{job.range_start}</span>
                        <span>至 {job.range_end}</span>
                      </td>
                      <td>
                        <span className={`job-status status-${job.status}`}>
                          {safeStopPending
                            ? "安全停止中"
                            : formatCollectionStatus(job.status)}
                        </span>
                        {job.queue_position ? <small>队列第 {job.queue_position} 位</small> : null}
                        {job.last_progress_at ? <small>更新 {job.last_progress_at}</small> : null}
                      </td>
                      <td>
                        <strong>{job.page_count ?? 0} 页</strong>
                        <span>最早：{job.checkpoint_oldest_at || "尚未取得消息"}</span>
                        <small>断点：{job.next_max_mid ?? "0"}</small>
                      </td>
                      <td>
                        <span>看到 {job.total_seen_count ?? 0} · 新增 {job.inserted_count ?? 0}</span>
                        <span>
                          重复 {job.duplicate_count ?? job.skipped_count ?? 0} · 失败 {job.failed_count ?? 0}
                        </span>
                        <small>
                          红包 {job.filtered_red_packet_count ?? 0} · 系统通知{" "}
                          {job.filtered_system_notice_count ?? 0}
                        </small>
                      </td>
                      <td>
                        {job.stop_code ? <strong>{job.stop_code}</strong> : null}
                        <span>{stopReason || "—"}</span>
                        {job.last_http_status ? <small>HTTP {job.last_http_status}</small> : null}
                        {job.last_error_code ? <small>API {job.last_error_code}</small> : null}
                        {job.resume_not_before ? (
                          <small>
                            风控冷却至 {job.resume_not_before}
                            {cooldownActive ? "（期间不可续传）" : "（已结束，可人工续传）"}
                          </small>
                        ) : null}
                      </td>
                      <td>
                        <div className="job-actions">
                          {job.status === "awaiting_confirmation" ? (
                            <>
                              <button
                                className="icon-button compact-button"
                                type="button"
                                disabled={jobActionId === job.id}
                                onClick={() => void runCollectionJobAction(job, "confirm")}
                              >
                                确认并启动
                              </button>
                              <button
                                className="secondary-button compact-button"
                                type="button"
                                disabled={jobActionId === job.id}
                                onClick={() => void runCollectionJobAction(job, "stop")}
                              >
                                暂停等待
                              </button>
                            </>
                          ) : null}
                          {job.status === "queued" ? (
                            <button
                              className="secondary-button compact-button"
                              type="button"
                              disabled={jobActionId === job.id}
                              onClick={() => void runCollectionJobAction(job, "stop")}
                            >
                              移出队列
                            </button>
                          ) : null}
                          {job.status === "running" ? (
                            <button
                              className="secondary-button compact-button"
                              type="button"
                              disabled={jobActionId === job.id || safeStopPending}
                              onClick={() => void runCollectionJobAction(job, "stop")}
                            >
                              {safeStopPending ? "正在安全停止" : "安全停止"}
                            </button>
                          ) : null}
                          {job.status === "stopped" ? (
                            <button
                              className="secondary-button compact-button"
                              type="button"
                              disabled={jobActionId === job.id || cooldownActive}
                              onClick={() => void runCollectionJobAction(job, "resume")}
                              title={cooldownActive ? "风控冷却结束后仍需人工续传" : undefined}
                            >
                              {cooldownActive ? "冷却中" : "沿断点续传"}
                            </button>
                          ) : null}
                          {!["queued", "awaiting_confirmation", "running", "stopped"].includes(job.status) ? (
                            <span className="muted">无操作</span>
                          ) : null}
                          {job.attempt_count > 0 ? (
                            <button
                              className="secondary-button compact-button"
                              type="button"
                              disabled={attemptLoadingJobId === job.id}
                              onClick={() => void toggleCollectionJobAttempts(job.id)}
                            >
                              {expandedJobId === job.id ? "收起运行记录" : "查看每次运行"}
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                    {expandedJobId === job.id ? (
                      <tr className="attempt-detail-row">
                        <td colSpan={7}>
                          <div className="attempt-history">
                            <strong>任务 #{job.id} 的每次运行</strong>
                            {attemptLoadingJobId === job.id ? (
                              <span className="muted">正在加载…</span>
                            ) : attempts.length === 0 ? (
                              <span className="muted">暂无运行记录</span>
                            ) : (
                              <div className="attempt-table-wrap">
                                <table className="attempt-table">
                                  <thead>
                                    <tr>
                                      <th>次数 / 状态</th>
                                      <th>开始 / 结束</th>
                                      <th>断点</th>
                                      <th>页数 / 计数</th>
                                      <th>过滤</th>
                                      <th>停止原因</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {attempts.map((attempt) => (
                                      <tr key={attempt.id}>
                                        <td>
                                          <strong>第 {attempt.attempt_no} 次</strong>
                                          <span>{formatCollectionStatus(attempt.status)}</span>
                                        </td>
                                        <td>
                                          <span>{attempt.started_at}</span>
                                          <span>{attempt.finished_at || "运行中"}</span>
                                        </td>
                                        <td>
                                          <span>{attempt.start_max_mid || "0"}</span>
                                          <span>→ {attempt.end_max_mid || attempt.start_max_mid || "0"}</span>
                                        </td>
                                        <td>
                                          <span>{attempt.page_count} 页 · 看到 {attempt.total_seen_count}</span>
                                          <span>新增 {attempt.inserted_count} · 重复 {attempt.duplicate_count}</span>
                                        </td>
                                        <td>
                                          <span>红包 {attempt.filtered_red_packet_count}</span>
                                          <span>系统通知 {attempt.filtered_system_notice_count}</span>
                                        </td>
                                        <td>
                                          <strong>{attempt.stop_code || "—"}</strong>
                                          <span>{attempt.stop_reason || "—"}</span>
                                          {attempt.last_http_status ? (
                                            <small>HTTP {attempt.last_http_status}</small>
                                          ) : null}
                                          {attempt.last_error_code ? (
                                            <small>API {attempt.last_error_code}</small>
                                          ) : null}
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    ) : null}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
            {!jobLoading && collectionJobs.length === 0 ? (
              <div className="empty-state">没有符合条件的采集任务</div>
            ) : null}
          </div>
          <div className="job-pagination">
            <button
              className="secondary-button compact-button"
              type="button"
              disabled={jobLoading || collectionJobOffset === 0}
              onClick={() =>
                void loadCollectionJobs(
                  collectionJobStatusFilter,
                  false,
                  Math.max(0, collectionJobOffset - COLLECTION_JOB_PAGE_SIZE),
                )
              }
            >
              上一页
            </button>
            <span>
              {collectionJobTotal === 0 ? 0 : collectionJobOffset + 1}–
              {Math.min(collectionJobOffset + collectionJobs.length, collectionJobTotal)} / {collectionJobTotal}
            </span>
            <button
              className="secondary-button compact-button"
              type="button"
              disabled={
                jobLoading ||
                collectionJobOffset + collectionJobs.length >= collectionJobTotal
              }
              onClick={() =>
                void loadCollectionJobs(
                  collectionJobStatusFilter,
                  false,
                  collectionJobOffset + COLLECTION_JOB_PAGE_SIZE,
                )
              }
            >
              下一页
            </button>
          </div>
        </section>
      ) : viewMode === "tools" ? (
        <section className="advanced-tools-workspace">
          <aside className="job-form-panel">
            <div className="panel-title">
              <h2>API 采集目标</h2>
            </div>
            <GroupLimitNotice data={options} accountId={collectionJobForm.accountId}/>
            <label>
              <span>现有账号（留空可新增）</span>
              <select
                value={collectionJobForm.accountId}
                onChange={(event) => {
                  const accountId = event.target.value;
                  const groupId = String(
                    options.groups.find((group) => String(group.account_id) === accountId)?.id ?? "",
                  );
                  setCollectionJobForm((current) => ({ ...current, accountId, groupId }));
                  setApiTargetForm(resolveApiTargetForm(options, accountId, groupId));
                }}
              >
                <option value="">新增账号</option>
                {options.accounts.map((account) => (
                  <option key={account.id} value={account.id}>{account.display_name}</option>
                ))}
              </select>
            </label>
            <label>
              <span>现有群聊（无群账号可新增）</span>
              <select
                value={collectionJobForm.groupId}
                onChange={(event) => {
                  const groupId = event.target.value;
                  setCollectionJobForm((current) => ({ ...current, groupId }));
                  setApiTargetForm(resolveApiTargetForm(options, collectionJobForm.accountId, groupId));
                }}
              >
                <option value="" disabled={!!collectionJobForm.accountId && visibleCollectionGroups.length>0}>新增群聊</option>
                {visibleCollectionGroups.map((group) => (
                  <option key={group.id} value={group.id}>{group.name}</option>
                ))}
              </select>
            </label>
            <label>
              <span>账号显示名称</span>
              <input
                value={apiTargetForm.accountName}
                onChange={(event) => setApiTargetForm((current) => ({ ...current, accountName: event.target.value }))}
                placeholder="例如：微博账号 A"
              />
            </label>
            <label>
              <span>群聊名称</span>
              <input
                value={apiTargetForm.groupName}
                onChange={(event) => setApiTargetForm((current) => ({ ...current, groupName: event.target.value }))}
                placeholder="微博中显示的群名"
              />
            </label>
            <label>
              <span>微博群 ID</span>
              <input
                disabled={hasMultipleGroups(options,collectionJobForm.accountId)}
                inputMode="numeric"
                value={apiTargetForm.sourceGroupId}
                onChange={(event) => setApiTargetForm((current) => ({ ...current, sourceGroupId: event.target.value }))}
                placeholder="query_messages 请求中的 id"
              />
            </label>
            <button
              className="secondary-button full-width-button"
              type="button"
              onClick={saveApiTarget}
              disabled={jobLoading}
            >
              <Save size={16} aria-hidden="true" />
              保存账号与群 ID
            </button>
            <details className="legacy-tools">
              <summary>备用：导入已有 Cookie 文件</summary>
              <label>
                <span>当前账号 Cookie 文件</span>
                <input
                  type="file"
                  accept="application/json,.json"
                  onChange={(event) => {
                    void importAccountCookies(event.target.files?.[0]);
                    event.currentTarget.value = "";
                  }}
                  disabled={jobLoading || !collectionJobForm.accountId}
                />
              </label>
              {cookieFileName ? <p className="form-hint">最近导入：{cookieFileName}</p> : null}
              <p className="form-hint">
                仅在内置浏览器助手不可用时使用；文件仍由同一后端安全规则校验和保存。
              </p>
            </details>
          </aside>

          <section className="job-form-panel">
            <div className="panel-title">
              <h2>JSON / CSV 数据导入</h2>
            </div>
            <label>
              <span>账号</span>
              <select
                value={collectionJobForm.accountId}
                onChange={(event) => {
                  const accountId = event.target.value;
                  const groupId = String(
                    options.groups.find((group) => String(group.account_id) === accountId)?.id ?? "",
                  );
                  setCollectionJobForm((current) => ({ ...current, accountId, groupId }));
                  setApiTargetForm(resolveApiTargetForm(options, accountId, groupId));
                }}
              >
                <option value="">选择账号</option>
                {options.accounts.map((account) => (
                  <option key={account.id} value={account.id}>{account.display_name}</option>
                ))}
              </select>
            </label>
            <label>
              <span>群聊</span>
              <select
                value={collectionJobForm.groupId}
                onChange={(event) => setCollectionJobForm((current) => ({ ...current, groupId: event.target.value }))}
              >
                <option value="">选择群聊</option>
                {visibleCollectionGroups.map((group) => (
                  <option key={group.id} value={group.id}>{group.name}</option>
                ))}
              </select>
            </label>
            <div className="tool-date-grid">
              <CollectionDateTime label="开始时间" value={collectionJobForm.rangeStart}
                onChange={value => setCollectionJobForm(current => ({ ...current, rangeStart: value }))} />
              <CollectionDateTime label="结束时间" value={collectionJobForm.rangeEnd}
                onChange={value => setCollectionJobForm(current => ({ ...current, rangeEnd: value }))} />
            </div>
            <label>
              <span>data/imports 中的文件</span>
              <select
                value={collectionJobForm.sourceFile}
                onChange={(event) =>
                  setCollectionJobForm((current) => ({
                    ...current,
                    sourceFile: event.target.value,
                  }))
                }
              >
                <option value="">选择 JSON 或 CSV 文件</option>
                {importFiles.map((file) => (
                  <option key={file.relative_path} value={file.relative_path}>
                    {file.name} · {formatFileSize(file.size)} · {file.modified_at}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="icon-button full-width-button"
              type="button"
              onClick={runSingleGroupFileCollection}
              disabled={jobLoading}
            >
              <Database size={16} aria-hidden="true" />
              执行文件导入
            </button>
            <details className="legacy-tools">
              <summary>调试工具</summary>
              <button
                className="secondary-button full-width-button"
                type="button"
                onClick={createCollectionJob}
                disabled={jobLoading}
              >
                <Clock size={16} aria-hidden="true" />
                仅创建空任务
              </button>
            </details>
          </section>

          <section className="job-list-panel capture-tools-panel">
            <div className="panel-title">
              <h2>网页快照导入</h2>
              <span>{browserCaptures.length} 个快照</span>
            </div>
            <div className="capture-tools-body">
              <p className="form-hint no-side-margin">
                使用上方数据导入区域所选的账号、群聊和时间范围生成脚本；
                本地导入不会占用微博 API 队列。
              </p>
              <div className="tool-button-row">
                <button
                  className="secondary-button"
                  type="button"
                  onClick={generateBrowserCaptureSnippet}
                  disabled={jobLoading}
                >
                  生成网页快照脚本
                </button>
                <button
                  className="secondary-button"
                  type="button"
                  onClick={() => void loadBrowserCaptures()}
                  disabled={jobLoading}
                >
                  刷新快照
                </button>
              </div>
              {browserCaptureSnippet ? (
                <div className="capture-script">
                  <textarea rows={6} readOnly value={browserCaptureSnippet} aria-label="网页快照脚本" />
                  <button className="secondary-button" type="button" onClick={() => void copyBrowserCaptureSnippet()}>
                    复制脚本
                  </button>
                </div>
              ) : null}

              <div className="capture-list">
                {browserCaptures.map((capture) => (
                  <article key={capture.id}>
                    <strong>#{capture.id} · {capture.group_name}</strong>
                    <span>{capture.account_name} · {capture.captured_at}</span>
                    <span>{capture.range_start} 至 {capture.range_end} · {capture.text_length} 字符</span>
                    <button
                      className="secondary-button compact-button"
                      type="button"
                      onClick={() => void previewBrowserCapture(capture.id)}
                      disabled={jobLoading}
                    >
                      解析预览
                    </button>
                  </article>
                ))}
                {!jobLoading && browserCaptures.length === 0 ? <p className="muted">还没有网页快照</p> : null}
              </div>

              {browserCapturePreview ? (
                <div className="capture-preview">
                  <div className="capture-preview-header">
                    <strong>快照 #{browserCapturePreview.capture_id} 解析预览</strong>
                    <span>
                      解析 {browserCapturePreview.parsed_count} · 重复{" "}
                      {browserCapturePreview.duplicate_count} · 跳过{" "}
                      {browserCapturePreview.skipped_blocks}
                    </span>
                  </div>
                  <div className="capture-preview-list">
                    {browserCapturePreview.items.map((item) => (
                      <article key={`${item.index}-${item.source_message_id}`}>
                        <strong>{item.sender_name}</strong>
                        <span>{item.sent_at} · {item.message_type}</span>
                        <p>{item.content_text}</p>
                        {item.is_duplicate ? <em>数据库中已存在</em> : null}
                      </article>
                    ))}
                  </div>
                  <button className="icon-button" type="button" onClick={importBrowserCapture} disabled={jobLoading}>
                    导入该网页快照
                  </button>
                </div>
              ) : null}
            </div>
          </section>
        </section>
      ) : viewMode === "weibo" ? (
        <section className="verification-workspace">
          <aside className="verification-form-panel">
            <div className="panel-title">
              <h2>创建验证记录</h2>
            </div>

            <label>
              <span>账号</span>
              <select
                value={verificationForm.accountId}
                onChange={(event) =>
                  setVerificationForm((current) => ({
                    ...current,
                    accountId: event.target.value,
                    groupId: "",
                  }))
                }
              >
                <option value="">选择账号</option>
                {options.accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.display_name}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span>群聊</span>
              <select
                value={verificationForm.groupId}
                onChange={(event) =>
                  setVerificationForm((current) => ({
                    ...current,
                    groupId: event.target.value,
                  }))
                }
              >
                <option value="">选择群聊</option>
                {visibleVerificationGroups.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.name}
                  </option>
                ))}
              </select>
            </label>

            <label>
              <span>核验历史天数</span>
              <input
                min="0"
                type="number"
                value={verificationForm.historyDaysChecked}
                onChange={(event) =>
                  setVerificationForm((current) => ({
                    ...current,
                    historyDaysChecked: event.target.value,
                  }))
                }
              />
            </label>

            <label>
              <span>风险级别</span>
              <select
                value={verificationForm.riskLevel}
                onChange={(event) =>
                  setVerificationForm((current) => ({
                    ...current,
                    riskLevel: event.target.value as VerificationReportForm["riskLevel"],
                  }))
                }
              >
                <option value="unknown">未确认</option>
                <option value="low">低</option>
                <option value="medium">中</option>
                <option value="high">高</option>
              </select>
            </label>

            <label>
              <span>验证状态</span>
              <select
                value={verificationForm.verificationStatus}
                onChange={(event) =>
                  setVerificationForm((current) => ({
                    ...current,
                    verificationStatus:
                      event.target.value as VerificationReportForm["verificationStatus"],
                  }))
                }
              >
                <option value="draft">记录中</option>
                <option value="verified">已验证</option>
                <option value="blocked">受阻</option>
              </select>
            </label>

            <label>
              <span>备注</span>
              <textarea
                rows={4}
                value={verificationForm.notes}
                onChange={(event) =>
                  setVerificationForm((current) => ({
                    ...current,
                    notes: event.target.value,
                  }))
                }
                placeholder="只记录现象和字段含义，不粘贴 Cookie、Token、Authorization。"
              />
            </label>

            <button
              className="icon-button full-width-button"
              type="button"
              onClick={createVerificationReport}
              disabled={verificationLoading}
            >
              <Plus size={16} aria-hidden="true" />
              创建记录
            </button>

            <p className="form-hint">
              每个微博账号对应一个群聊时，建议分别创建记录，所有结果会进入同一个 SQLite 数据库。
            </p>
          </aside>

          <section className="verification-list-panel">
            <div className="panel-title">
              <h2>验证记录</h2>
              <span>
                {verificationLoading ? "加载中" : `${verificationReports.length} / ${verificationTotal}`}
              </span>
            </div>

            <div className="job-toolbar">
              <select
                value={verificationStatusFilter}
                onChange={(event) => {
                  setVerificationStatusFilter(event.target.value);
                  void loadVerificationReports(event.target.value);
                }}
              >
                <option value="">全部状态</option>
                <option value="draft">记录中</option>
                <option value="verified">已验证</option>
                <option value="blocked">受阻</option>
              </select>
            </div>

            <div className="verification-list">
              {verificationReports.map((report) => (
                <button
                  className={
                    selectedVerificationId === report.id
                      ? "verification-card selected"
                      : "verification-card"
                  }
                  key={report.id}
                  type="button"
                  onClick={() => void loadVerificationDetail(report.id)}
                >
                  <div className="job-card-header">
                    <strong>#{report.id} {report.group_name}</strong>
                    <span className={`job-status status-${report.verification_status}`}>
                      {formatVerificationStatus(report.verification_status)}
                    </span>
                  </div>
                  <dl>
                    <div>
                      <dt>账号</dt>
                      <dd>{report.account_name}</dd>
                    </div>
                    <div>
                      <dt>核验天数</dt>
                      <dd>{report.history_days_checked ?? "未填"}</dd>
                    </div>
                    <div>
                      <dt>风险</dt>
                      <dd>{formatRiskLevel(report.risk_level)}</dd>
                    </div>
                    <div>
                      <dt>观察</dt>
                      <dd>{report.observation_count ?? 0} 条</dd>
                    </div>
                  </dl>
                </button>
              ))}

              {!verificationLoading && verificationReports.length === 0 ? (
                <div className="empty-state">还没有微博验证记录</div>
              ) : null}
            </div>
          </section>

          <aside className="verification-detail-panel">
            <div className="panel-title">
              <h2>验证详情</h2>
              <span>
                {verificationLoading ? "加载中" : selectedVerification ? `#${selectedVerification.id}` : "未选择"}
              </span>
            </div>

            {selectedVerification ? (
              <div className="verification-detail-body">
                <section className="verification-section">
                  <h3>验证结论</h3>
                  <div className="checkbox-grid">
                    {verificationBooleanFields.map((field) => (
                      <label className="checkbox-row" key={field.key}>
                        <input
                          checked={Boolean(selectedVerification[field.key])}
                          type="checkbox"
                          onChange={(event) =>
                            updateSelectedVerification(field.key, event.target.checked)
                          }
                        />
                        <span>{field.label}</span>
                      </label>
                    ))}
                  </div>

                  <label>
                    <span>核验历史天数</span>
                    <input
                      min="0"
                      type="number"
                      value={selectedVerification.history_days_checked ?? ""}
                      onChange={(event) =>
                        updateSelectedVerification(
                          "history_days_checked",
                          event.target.value ? Number(event.target.value) : null,
                        )
                      }
                    />
                  </label>

                  <label>
                    <span>风险级别</span>
                    <select
                      value={selectedVerification.risk_level}
                      onChange={(event) =>
                        updateSelectedVerification(
                          "risk_level",
                          event.target.value as WeiboVerificationReport["risk_level"],
                        )
                      }
                    >
                      <option value="unknown">未确认</option>
                      <option value="low">低</option>
                      <option value="medium">中</option>
                      <option value="high">高</option>
                    </select>
                  </label>

                  <label>
                    <span>验证状态</span>
                    <select
                      value={selectedVerification.verification_status}
                      onChange={(event) =>
                        updateSelectedVerification(
                          "verification_status",
                          event.target.value as WeiboVerificationReport["verification_status"],
                        )
                      }
                    >
                      <option value="draft">记录中</option>
                      <option value="verified">已验证</option>
                      <option value="blocked">受阻</option>
                    </select>
                  </label>

                  <label>
                    <span>备注</span>
                    <textarea
                      rows={4}
                      value={selectedVerification.notes ?? ""}
                      onChange={(event) =>
                        updateSelectedVerification("notes", event.target.value)
                      }
                    />
                  </label>

                  <button
                    className="icon-button full-width-button no-side-margin"
                    type="button"
                    onClick={saveVerificationReport}
                    disabled={verificationLoading}
                  >
                    <Save size={16} aria-hidden="true" />
                    保存结论
                  </button>
                </section>

                <section className="verification-section">
                  <h3>添加脱敏观察</h3>
                  <label>
                    <span>类型</span>
                    <select
                      value={observationForm.observationType}
                      onChange={(event) =>
                        setObservationForm((current) => ({
                          ...current,
                          observationType: event.target.value as ObservationForm["observationType"],
                        }))
                      }
                    >
                      <option value="message_history">历史消息</option>
                      <option value="pagination">分页</option>
                      <option value="image">图片</option>
                      <option value="file">文件</option>
                      <option value="link">链接</option>
                      <option value="red_packet">红包</option>
                      <option value="error">错误</option>
                      <option value="official_api">官方接口</option>
                    </select>
                  </label>

                  <div className="two-column-fields">
                    <label>
                      <span>方法</span>
                      <input
                        value={observationForm.method}
                        onChange={(event) =>
                          setObservationForm((current) => ({
                            ...current,
                            method: event.target.value,
                          }))
                        }
                        placeholder="GET / POST"
                      />
                    </label>
                    <label>
                      <span>路径</span>
                      <input
                        value={observationForm.endpointPath}
                        onChange={(event) =>
                          setObservationForm((current) => ({
                            ...current,
                            endpointPath: event.target.value,
                          }))
                        }
                        placeholder="/example/path"
                      />
                    </label>
                  </div>

                  <label>
                    <span>请求字段 JSON</span>
                    <textarea
                      rows={3}
                      value={observationForm.requestFields}
                      onChange={(event) =>
                        setObservationForm((current) => ({
                          ...current,
                          requestFields: event.target.value,
                        }))
                      }
                      placeholder='{"group_id":"脱敏群 ID","start_time":"2026-06-01 00:00:00"}'
                    />
                  </label>

                  <label>
                    <span>响应字段 JSON</span>
                    <textarea
                      rows={3}
                      value={observationForm.responseFields}
                      onChange={(event) =>
                        setObservationForm((current) => ({
                          ...current,
                          responseFields: event.target.value,
                        }))
                      }
                      placeholder='{"messages":[],"next_cursor":"脱敏游标"}'
                    />
                  </label>

                  <label>
                    <span>样例内容 JSON</span>
                    <textarea
                      rows={4}
                      value={observationForm.samplePayload}
                      onChange={(event) =>
                        setObservationForm((current) => ({
                          ...current,
                          samplePayload: event.target.value,
                        }))
                      }
                      placeholder='{"message_id":"脱敏消息 ID","text":"示例正文"}'
                    />
                  </label>

                  <label>
                    <span>分页字段 JSON</span>
                    <textarea
                      rows={3}
                      value={observationForm.paginationFields}
                      onChange={(event) =>
                        setObservationForm((current) => ({
                          ...current,
                          paginationFields: event.target.value,
                        }))
                      }
                      placeholder='{"cursor":"脱敏游标","has_more":true}'
                    />
                  </label>

                  <label>
                    <span>脱敏备注</span>
                    <textarea
                      rows={3}
                      value={observationForm.redactionNotes}
                      onChange={(event) =>
                        setObservationForm((current) => ({
                          ...current,
                          redactionNotes: event.target.value,
                        }))
                      }
                    />
                  </label>

                  <button
                    className="secondary-button full-width-button no-side-margin"
                    type="button"
                    onClick={createObservation}
                    disabled={verificationLoading}
                  >
                    <Plus size={16} aria-hidden="true" />
                    添加观察
                  </button>
                </section>

                <section className="verification-section">
                  <h3>已有观察</h3>
                  <div className="observation-list">
                    {(selectedVerification.observations ?? []).map((observation) => (
                      <article className="observation-item" key={observation.id}>
                        <div className="job-card-header">
                          <strong>{formatObservationType(observation.observation_type)}</strong>
                          <span>{observation.created_at}</span>
                        </div>
                        <dl>
                          <div>
                            <dt>方法</dt>
                            <dd>{observation.method || "未填"}</dd>
                          </div>
                          <div>
                            <dt>路径</dt>
                            <dd>{observation.endpoint_path || "未填"}</dd>
                          </div>
                        </dl>
                        {observation.redaction_notes ? (
                          <p className="form-hint no-side-margin">{observation.redaction_notes}</p>
                        ) : null}
                        {observation.sample_payload ? (
                          <pre>{stringifyJsonPreview(observation.sample_payload)}</pre>
                        ) : null}
                      </article>
                    ))}

                    {(selectedVerification.observations ?? []).length === 0 ? (
                      <p className="muted">还没有接口或页面观察记录</p>
                    ) : null}
                  </div>
                </section>
              </div>
            ) : (
              <div className="empty-state">请选择或创建一条验证记录</div>
            )}
          </aside>
        </section>
      ) : (["active", "deleted"].includes(viewMode) &&
        <MessageWorkspace
          externalMessage={externalMessage}
          key={JSON.stringify([appliedFilters, viewMode])}
          appliedFilters={appliedFilters}
          filters={filters}
          options={options}
          updateFilter={updateFilter}
          applySearch={applySearch}
          resetSearch={resetSearch}
          viewMode={viewMode}
          deleting={deleting}
          previewAction={previewAction}
          actionPreview={actionPreview}
          executeSoftDelete={executeSoftDelete}
          executeRestore={executeRestore}
          executeHardDelete={executeHardDelete}
          loading={loading}
          messages={messages}
          total={total}
          attachmentSummary={attachmentSummary}
          selectedMessageId={selectedMessageId}
          setSelectedMessageId={setSelectedMessageId}
          loadMore={loadMore}
          hasMore={hasMore}
          loadingMore={loadingMore}
          detailLoading={detailLoading}
          detail={detail}
        />
      )}
    </main>
  );
}
