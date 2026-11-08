import type { Message } from "../../shared/contracts";

export function isContinuation(previous: Message | undefined, current: Message): boolean {
  if (!previous || !current.user_id || !previous.user_id) return false;
  const sameIdentity = previous.account_id === current.account_id &&
    previous.group_id === current.group_id && previous.user_id === current.user_id;
  const gap = Math.abs(Date.parse(previous.sent_at.replace(" ", "T")) - Date.parse(current.sent_at.replace(" ", "T")));
  return sameIdentity && previous.sent_at.slice(0, 10) === current.sent_at.slice(0, 10) && gap <= 300_000;
}

export function splitQuote(content: string): { quote: string; reply: string } | null {
  const text = content.trim();
  if (!text.startsWith("「")) return null;
  let depth = 0;
  for (let index = 0; index < text.length; index++) {
    if (text[index] === "「") depth++;
    if (text[index] === "」") depth--;
    if (depth !== 0) continue;
    const rest = text.slice(index + 1);
    const separator = rest.match(/^(?:\s*-\s*){3,}/);
    const quote = text.slice(1, index).trim();
    if (!separator || !quote) return null;
    return { quote, reply: rest.slice(separator[0].length).trim() };
  }
  return null;
}

function quoteKey(text: string): string {
  // Full text only: do not use a short prefix or remove meaningful internal spaces.
  return text.replace(/\r\n/g, "\n").trim();
}

function scopeKey(message: Message, content: string): string {
  return JSON.stringify([message.account_id, message.group_id, message.is_deleted, quoteKey(content)]);
}

export function indexQuoteCandidates(messages: Message[]): Map<string, Message[]> {
  const index = new Map<string, Message[]>();
  for (const message of messages) {
    const key = scopeKey(message, message.content_text);
    const bucket = index.get(key) ?? [];
    bucket.push(message);
    index.set(key, bucket);
  }
  return index;
}

export function resolveQuote(message: Message, quote: string, index: Map<string, Message[]>): Message[] {
  const candidates = index.get(scopeKey(message, quote)) ?? [];
  const sentAt = Date.parse(message.sent_at.replace(" ", "T"));
  // Equal timestamps have no reliable source order; do not infer from local database IDs.
  return candidates.filter((candidate) => candidate.id !== message.id &&
    Date.parse(candidate.sent_at.replace(" ", "T")) < sentAt);
}

export function safeHttpUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
    return url.href;
  } catch {
    return null;
  }
}

export function bodyLinks(text: string): string[] {
  const matches = text.match(/https?:\/\/[^\s<>"'「」]+/gi) ?? [];
  const links = matches.map((match) => safeHttpUrl(match.replace(/[。，！？、；：）】》]+$/u, "")));
  return [...new Set(links.filter((link): link is string => link !== null))];
}

export function chronological<T extends Pick<Message, 'sent_at' | 'id'>>(messages: T[]): T[] {
  return [...messages].sort((a, b) => a.sent_at.localeCompare(b.sent_at) || a.id - b.id);
}
