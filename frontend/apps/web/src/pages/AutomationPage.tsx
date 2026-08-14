import {
  scheduledInputLastRunText,
  toScheduledInputListItemViewModel,
  type ScheduledInputCommand,
  type ScheduledInputRule,
  type TransactionType,
} from "@family-spending/core";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { familySpendingService } from "../api/client";
import { Button } from "../components/ui/Button";
import "./automation.css";

/** Pick a V1-safe monthly occurrence date without silently creating a day 29-31 rule. */
function defaultScheduledDate(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  if (local.getDate() <= 28) return local.toISOString().slice(0, 10);
  const nextMonth = new Date(local.getFullYear(), local.getMonth() + 1, 1);
  const nextLocal = new Date(nextMonth.getTime() - nextMonth.getTimezoneOffset() * 60_000);
  return nextLocal.toISOString().slice(0, 10);
}

export function AutomationPage() {
  const [rules, setRules] = useState<readonly ScheduledInputRule[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const load = useCallback(async (preferredId?: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const next = await familySpendingService.listScheduledInputs();
      setRules(next);
      const wanted = preferredId ?? selectedId;
      setSelectedId(wanted && next.some((rule) => rule.id === wanted) ? wanted : next[0]?.id ?? null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => { void load(); }, []);

  const selected = rules.find((rule) => rule.id === selectedId) ?? null;

  async function runDue(): Promise<void> {
    setRunning(true);
    setNotice("正在执行所有已到期 occurrence…");
    try {
      const result = await familySpendingService.runDueScheduledInputs();
      setNotice(result.generated_count === 0 ? "当前没有到期 occurrence。" : `已处理 ${result.generated_count} 个 occurrence；交易与统计已刷新。`);
      await load(selectedId);
    } catch (caught) {
      setNotice(`执行失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="automation-workspace">
      <section className="automation-create">
        <div className="section-heading">
          <div><h2>Scheduled Input</h2><p>按月生成普通 Manual Source；V1 的 recurrence day 固定在每月 1–28 日。</p></div>
          <Button disabled={running} onClick={() => void runDue()}>{running ? "执行中…" : "执行到期项"}</Button>
        </div>
        <ScheduledRuleForm
          mode="create"
          initialRule={null}
          onSaved={async (rule) => {
            setNotice(rule.last_occurrence_date ? `规则已保存；最近已执行 ${rule.last_occurrence_date}，下一次 ${rule.next_date}。` : `规则已保存；下一次 ${rule.next_date}。`);
            await load(rule.id);
          }}
        />
        {notice ? <p className="automation-notice">{notice}</p> : null}
      </section>

      <div className="automation-split">
        <section className="automation-list-pane">
          <div className="automation-toolbar"><div><strong>{rules.length}</strong><span> 条规则</span></div><Button variant="ghost" disabled={loading} onClick={() => void load(selectedId)}>{loading ? "刷新中…" : "刷新"}</Button></div>
          {loading && rules.length === 0 ? <div className="page-state">正在读取 Scheduled Inputs…</div> : null}
          {error && rules.length === 0 ? <div className="page-state page-state--error"><p>{error}</p><Button onClick={() => void load()}>重试</Button></div> : null}
          {!loading && !error && rules.length === 0 ? <div className="automation-empty"><strong>还没有定期录入规则</strong><p>上方创建后，规则会按月通过 Manual Source 进入既有对账链路。</p></div> : null}
          <div className="automation-list">
            {rules.map((rule) => {
              const view = toScheduledInputListItemViewModel(rule);
              return (
                <button key={rule.id} type="button" className={`automation-row${selectedId === rule.id ? " automation-row--active" : ""}`} onClick={() => setSelectedId(rule.id)}>
                  <span className="automation-row__main"><strong>{view.description}</strong><small>{view.enabledLabel} · 下次 {view.nextDate}</small></span>
                  <span className="automation-row__value"><strong>{view.amountText}</strong><small>{view.typeLabel}</small></span>
                </button>
              );
            })}
          </div>
        </section>

        <section className="automation-detail-pane">
          {selected ? <ScheduledRuleDetail key={selected.id} rule={selected} onChanged={async (preferredId) => { await load(preferredId); }} setNotice={setNotice} /> : <div className="automation-detail-empty"><strong>选择一条规则</strong><p>查看最近执行状态，并只修改未来 occurrence 的配置。</p></div>}
        </section>
      </div>
    </div>
  );
}

function ScheduledRuleForm({
  mode,
  initialRule,
  onSaved,
}: {
  mode: "create" | "edit";
  initialRule: ScheduledInputRule | null;
  onSaved: (rule: ScheduledInputRule) => Promise<void>;
}) {
  const [type, setType] = useState<TransactionType>(initialRule?.type ?? "expense");
  const [amount, setAmount] = useState(initialRule?.amount ?? "");
  const [description, setDescription] = useState(initialRule?.description ?? "");
  const [nextDate, setNextDate] = useState(initialRule?.next_date ?? defaultScheduledDate());
  const [note, setNote] = useState(initialRule?.note ?? "");
  const [enabled, setEnabled] = useState(initialRule?.enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!amount.trim() || !description.trim() || !nextDate) {
      setStatus("请填写金额、description 和下次日期。");
      return;
    }
    const command: ScheduledInputCommand = {
      type,
      amount: amount.trim(),
      description: description.trim(),
      note: note.trim() || null,
      nextDate,
      enabled,
    };
    setBusy(true);
    setStatus(mode === "create" ? "正在保存规则并执行已到期 occurrence…" : "正在更新未来规则并执行已到期 occurrence…");
    try {
      const saved = mode === "create"
        ? await familySpendingService.createScheduledInput(command)
        : await familySpendingService.updateScheduledInput(initialRule!.id, command);
      setStatus("已保存。");
      if (mode === "create") {
        setType("expense"); setAmount(""); setDescription(""); setNextDate(defaultScheduledDate()); setNote(""); setEnabled(true);
      }
      await onSaved(saved);
    } catch (caught) {
      setStatus(`保存失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className={`scheduled-form scheduled-form--${mode}`} onSubmit={(event) => void submit(event)}>
      <label><span className="field-label">类型</span><select className="field-control" value={type} onChange={(event) => setType(event.target.value as TransactionType)}><option value="expense">支出</option><option value="income">收入</option></select></label>
      <label><span className="field-label">金额</span><input className="field-control" value={amount} inputMode="decimal" onChange={(event) => setAmount(event.target.value)} placeholder="88.50" /></label>
      <label className="scheduled-form__description"><span className="field-label">description</span><input className="field-control" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="例如：固定房租" /></label>
      <label><span className="field-label">下次日期</span><input className="field-control" type="date" value={nextDate} onChange={(event) => setNextDate(event.target.value)} /><small className="scheduled-field-hint">每月仅支持 1–28 日。</small></label>
      <label className="scheduled-form__note"><span className="field-label">Note</span><input className="field-control" value={note} onChange={(event) => setNote(event.target.value)} /></label>
      <label className="scheduled-toggle"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span>启用规则</span></label>
      {status ? <p className="form-status scheduled-form__status">{status}</p> : null}
      <div className="scheduled-form__actions"><Button type="submit" variant="primary" disabled={busy}>{busy ? "处理中…" : mode === "create" ? "创建规则" : "保存未来规则"}</Button></div>
    </form>
  );
}

function ScheduledRuleDetail({
  rule,
  onChanged,
  setNotice,
}: {
  rule: ScheduledInputRule;
  onChanged: (preferredId: string | null) => Promise<void>;
  setNotice: (message: string | null) => void;
}) {
  const [deleting, setDeleting] = useState(false);

  async function remove(): Promise<void> {
    if (!window.confirm(`删除 Scheduled Rule「${rule.description}」？\n已经生成的 Manual Source / Transaction 不会被删除。`)) return;
    setDeleting(true);
    try {
      await familySpendingService.deleteScheduledInput(rule.id);
      setNotice("规则已删除；历史 occurrence 保持不变。");
      await onChanged(null);
    } catch (caught) {
      setNotice(`删除失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="automation-detail">
      <header className="automation-detail__header"><div><p className="eyebrow">Scheduled Rule</p><h2>{rule.description}</h2><p>{rule.id}</p></div><span className={`status-pill${rule.enabled ? " status-pill--open" : ""}`}>{rule.enabled ? "启用" : "暂停"}</span></header>
      <dl className="automation-facts"><div><dt>下次 occurrence</dt><dd>{rule.next_date}</dd></div><div><dt>最近执行</dt><dd>{scheduledInputLastRunText(rule)}</dd></div></dl>
      <ScheduledRuleForm mode="edit" initialRule={rule} onSaved={async (updated) => { setNotice(updated.last_occurrence_date ? `规则已更新；最近 occurrence ${updated.last_occurrence_date}，下一次 ${updated.next_date}。` : `规则已更新；下一次 ${updated.next_date}。`); await onChanged(updated.id); }} />
      <div className="automation-delete"><div><strong>删除未来规则</strong><p>只删除 orchestration rule；已经生成的 Manual Source / Transaction 不会被删除。</p></div><Button className="transaction-delete" disabled={deleting} onClick={() => void remove()}>{deleting ? "删除中…" : "删除规则"}</Button></div>
    </div>
  );
}
