import {
  findSimilarMerchantNames,
  mappingReviewImpactLines,
  toMappingReviewListItemViewModel,
  type MappingReviewItem,
  type MappingReviewPreview,
  type MappingReviewWorkspace,
} from "@family-spending/core";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router";

import { familySpendingService } from "../api/client";
import { Button } from "../components/ui/Button";
import "./review.css";

export function ReviewPage() {
  const [workspace, setWorkspace] = useState<MappingReviewWorkspace | null>(null);
  const [selectedDescription, setSelectedDescription] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async (preferredDescription?: string | null) => {
    setLoading(true);
    setError(null);
    try {
      const next = await familySpendingService.getMappingReviewWorkspace();
      setWorkspace(next);
      const preferred = preferredDescription ?? null;
      const selected =
        preferred && next.items.some((item) => item.description === preferred)
          ? preferred
          : next.items[0]?.description ?? null;
      setSelectedDescription(selected);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const selected = workspace?.items.find((item) => item.description === selectedDescription) ?? null;

  async function applied(): Promise<void> {
    setNotice("Mapping 已应用；审核队列和下游统计已刷新。");
    await load();
  }

  return (
    <div className="review-workspace">
      <section className="review-list-pane">
        <div className="review-toolbar">
          <div><strong>{workspace?.items.length ?? 0}</strong><span> 个待审核 description</span></div>
          <Button variant="ghost" disabled={loading} onClick={() => void load(selectedDescription)}>
            {loading ? "刷新中…" : "刷新"}
          </Button>
        </div>

        {notice ? <p className="review-notice">{notice}</p> : null}
        {loading && !workspace ? <div className="page-state">正在读取审核队列…</div> : null}
        {error && !workspace ? (
          <div className="page-state page-state--error"><p>{error}</p><Button onClick={() => void load()}>重试</Button></div>
        ) : null}
        {workspace && workspace.items.length === 0 ? (
          <div className="review-empty"><strong>没有待分类 Expense</strong><p>Income 不进入 Merchant Mapping，因此不会出现在这里。</p></div>
        ) : null}

        <div className="review-list">
          {workspace?.items.map((item) => (
            <ReviewRow
              key={item.description}
              item={item}
              selected={item.description === selectedDescription}
              onSelect={() => { setNotice(null); setSelectedDescription(item.description); }}
            />
          ))}
        </div>
      </section>

      <section className="review-detail-pane">
        {selected && workspace ? (
          <ReviewDetail key={selected.description} item={selected} workspace={workspace} onApplied={applied} />
        ) : (
          <div className="review-detail-empty">
            <strong>{workspace?.items.length === 0 ? "审核队列已清空" : "选择一个待审核 description"}</strong>
            <p>稳定的 description → Merchant → 默认 Category 在这里处理；单笔例外继续留在 Transactions。</p>
          </div>
        )}
      </section>
    </div>
  );
}

function ReviewRow({
  item,
  selected,
  onSelect,
}: {
  item: MappingReviewItem;
  selected: boolean;
  onSelect: () => void;
}) {
  const view = toMappingReviewListItemViewModel(item);
  return (
    <button type="button" className={`review-row${selected ? " review-row--active" : ""}`} onClick={onSelect}>
      <span className="review-row__main">
        <strong>{view.description}</strong>
        <small>{view.latestDate} · {view.sourceTypesText}</small>
      </span>
      <span className="review-row__value">
        <strong>{view.amountText}</strong>
        <small>{view.transactionCountText}{view.transactionOnlyExceptionCount > 0 ? ` · ${view.transactionOnlyExceptionCount} 笔单笔例外` : ""}</small>
      </span>
    </button>
  );
}

function ReviewDetail({
  item,
  workspace,
  onApplied,
}: {
  item: MappingReviewItem;
  workspace: MappingReviewWorkspace;
  onApplied: () => Promise<void>;
}) {
  const [merchant, setMerchant] = useState("");
  const [category, setCategory] = useState("");
  const [preview, setPreview] = useState<MappingReviewPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  const exactMerchant = workspace.merchants.find((option) => option.name === merchant.trim()) ?? null;
  const suggestions = useMemo(
    () => findSimilarMerchantNames(merchant, workspace.merchants).filter((name) => name !== merchant.trim()),
    [merchant, workspace.merchants],
  );

  function invalidatePreview(): void {
    setPreview(null);
    setStatus(null);
  }

  function chooseMerchant(name: string): void {
    const option = workspace.merchants.find((candidate) => candidate.name === name);
    setMerchant(name);
    if (option) setCategory(option.default_category);
    invalidatePreview();
  }

  async function previewMapping(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const nextMerchant = merchant.trim();
    if (!nextMerchant || !category) {
      setStatus("请选择 Merchant 和默认 Category 后再预览。");
      return;
    }
    setBusy(true);
    setStatus("正在计算 Mapping 影响范围…");
    try {
      const result = await familySpendingService.previewMappingReview({
        description: item.description,
        merchant: nextMerchant,
        category,
      });
      setPreview(result);
      setStatus("预览已锁定；修改 Merchant 或 Category 后需要重新预览。");
    } catch (caught) {
      setPreview(null);
      setStatus(`预览失败：${caught instanceof Error ? caught.message : String(caught)}`);
    } finally {
      setBusy(false);
    }
  }

  async function applyMapping(): Promise<void> {
    if (!preview) {
      setStatus("请先预览影响范围。");
      return;
    }
    let confirmNewMerchant = false;
    if (preview.is_new_merchant) {
      confirmNewMerchant = window.confirm(
        `将创建新 Merchant「${preview.merchant}」，并把 description「${preview.description}」映射到它。确认继续？`,
      );
      if (!confirmNewMerchant) {
        setStatus("已取消新 Merchant 创建。");
        return;
      }
    }

    setBusy(true);
    setStatus("正在写入 Mapping、传播 Enrichment 并刷新统计…");
    try {
      await familySpendingService.applyMappingReview({
        description: preview.description,
        merchant: preview.merchant,
        category: preview.category,
        previewToken: preview.token,
        confirmNewMerchant,
      });
      await onApplied();
    } catch (caught) {
      setStatus(`应用失败：${caught instanceof Error ? caught.message : String(caught)}`);
      setBusy(false);
    }
  }

  const row = toMappingReviewListItemViewModel(item);
  const impact = preview ? mappingReviewImpactLines(preview) : [];

  return (
    <div className="review-detail">
      <header className="review-detail__header">
        <div><p className="eyebrow">Mapping Review</p><h2>{item.description}</h2><p>{row.transactionCountText} · 原始金额合计 {row.amountText} · 最近 {row.latestDate}</p></div>
        <span className="status-pill">{row.sourceTypesText}</span>
      </header>

      <div className="review-exception-note">
        {item.transaction_only_exception_count > 0
          ? `其中 ${item.transaction_only_exception_count} 笔已有 transaction-only Merchant 例外；Apply 不会覆盖这些 Merchant。`
          : "当前组没有 transaction-only Merchant 例外。"}
        <Link to="/transactions">单笔例外请到交易中处理</Link>
      </div>

      <form className="review-form" onSubmit={(event) => void previewMapping(event)}>
        <label>
          <span className="field-label">Merchant</span>
          <input
            className="field-control"
            value={merchant}
            placeholder="搜索已有 Merchant，或输入新 Merchant"
            onChange={(event) => {
              const value = event.target.value;
              setMerchant(value);
              if (!category) {
                const existing = workspace.merchants.find((option) => option.name === value.trim());
                if (existing) setCategory(existing.default_category);
              }
              invalidatePreview();
            }}
          />
        </label>

        <div className="review-merchant-hint">
          {!merchant.trim()
            ? "优先复用已有 Merchant；确实不存在时才新建。"
            : exactMerchant
              ? `已有 Merchant；当前默认 Category：${exactMerchant.default_category}。修改默认 Category 会影响其他仍跟随该 Merchant 默认值的交易。`
              : "当前名称会作为新 Merchant；Apply 前会再次确认。"}
        </div>

        {suggestions.length > 0 ? (
          <div className="review-suggestions">
            {suggestions.map((name) => {
              const option = workspace.merchants.find((candidate) => candidate.name === name);
              return <button type="button" key={name} className="review-suggestion" onClick={() => chooseMerchant(name)}>使用已有：{name}{option ? ` · ${option.default_category}` : ""}</button>;
            })}
          </div>
        ) : null}

        <label>
          <span className="field-label">默认 Category</span>
          <select className="field-control" value={category} onChange={(event) => { setCategory(event.target.value); invalidatePreview(); }}>
            <option value="">请选择默认 Category</option>
            {workspace.categories.map((item) => <option value={item} key={item}>{item}</option>)}
          </select>
        </label>

        {status ? <p className="form-status">{status}</p> : null}
        <div className="review-actions">
          <Button type="submit" disabled={busy}>{busy && !preview ? "预览中…" : "预览影响范围"}</Button>
          <Button type="button" variant="primary" disabled={!preview || busy} onClick={() => void applyMapping()}>{busy && preview ? "应用中…" : "应用 Mapping"}</Button>
        </div>
      </form>

      {preview ? (
        <section className="review-impact">
          <div className="section-heading"><div><h3>影响预览</h3><p>Preview token 绑定当前 Mapping 选择和状态；输入变化后必须重新预览。</p></div></div>
          <ul>{impact.map((line, index) => <li key={`${line.text}-${index}`} className={line.emphasis ? "review-impact__emphasis" : ""}>{line.text}</li>)}</ul>
        </section>
      ) : null}
    </div>
  );
}