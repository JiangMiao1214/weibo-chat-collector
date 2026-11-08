import { useEffect, useRef, useState } from "react";

export function TimeSelect({ label, value, options, unit, onChange }: {
  label: string; value: string; options: string[]; unit: string; onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [above, setAbove] = useState(false);
  const [dialogTop, setDialogTop] = useState<number | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLSelectElement>(null);
  useEffect(() => { if (open) list.current?.focus(); }, [open]);
  function close() {
    setOpen(false);
    button.current?.focus();
  }
  function toggle() {
    const bottom = button.current?.getBoundingClientRect().bottom ?? 0;
    setAbove(window.innerHeight - bottom < 340);
    const bounds = button.current?.closest('dialog')?.getBoundingClientRect();
    const buttonTop = button.current?.getBoundingClientRect().top ?? 0;
    // Dialogs scroll and clip descendants; keep the whole list inside their border.
    setDialogTop(bounds ? Math.max(bounds.top + 2, Math.min(bottom + 4, bounds.bottom - 340)) - buttonTop : null);
    setOpen(!open);
  }
  return <div className="collection-time-select">
    <button ref={button} type="button" aria-label={`${label}选择`} aria-haspopup="listbox"
      aria-expanded={open} onClick={toggle}>{value} {unit} ▾</button>
    {open && <select ref={list} className={above ? "time-options above" : "time-options"}
      style={dialogTop === null ? undefined : {top: dialogTop, bottom: "auto"}}
      aria-label={label} size={12} value={value}
      onChange={event => onChange(event.currentTarget.value)}
      onClick={event => { if (event.target instanceof HTMLOptionElement) close(); }} onBlur={() => setOpen(false)}
      onKeyDown={event => {
        if (event.key === "Enter" || event.key === "Escape") { event.preventDefault(); close(); }
      }}>
      {options.map(item => <option key={item} value={item}>{item} {unit}</option>)}
    </select>}
  </div>;
}
