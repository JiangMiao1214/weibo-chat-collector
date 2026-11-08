import { TimeSelect } from "./TimeSelect";
import { useState } from "react";

const hours = Array.from({ length: 24 }, (_, n) => String(n).padStart(2, "0"));
const minutes = Array.from({ length: 60 }, (_, n) => String(n).padStart(2, "0"));

// Keep time selectable even before a date exists; only emit complete local dates.
export function CollectionDateTime({ label, value, max, onChange }: {
  label: string; value: string; max?: string; onChange: (value: string) => void;
}) {
  const [draftTime, setDraftTime] = useState("00:00");
  const [date, time = draftTime] = value.split("T");
  const [hour, minute] = time.split(":");
  function update(nextDate: string, nextHour: string, nextMinute: string) {
    const nextTime = `${nextHour}:${nextMinute}`;
    setDraftTime(nextTime);
    onChange(nextDate ? `${nextDate}T${nextTime}` : "");
  }
  return <div className="collection-date-time" role="group" aria-label={label}>
    <span>{label}</span>
    <div className="collection-date-time-fields">
      <input type="date" aria-label={`${label}日期`} value={date} max={max?.slice(0, 10)}
        onChange={event => update(event.currentTarget.value, hour, minute)} />
      <TimeSelect label={`${label}小时`} value={hour} options={hours} unit="时"
        onChange={next => update(date, next, minute)} />
      <TimeSelect label={`${label}分钟`} value={minute} options={minutes} unit="分"
        onChange={next => update(date, hour, next)} />
    </div>
  </div>;
}
