import {
  toFinancialSummaryViewModel,
  type FinancialSummaryViewModel,
} from "@family-spending/core";
import { useCallback, useEffect, useState } from "react";

import { familySpendingService } from "../api/client";
import { Button } from "../components/ui/Button";

/** Read-first Overview: all financial semantics come from the generated backend projection. */
export function OverviewPage() {
  const [summary, setSummary] = useState<FinancialSummaryViewModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await familySpendingService.getFinancialSummary();
      setSummary(toFinancialSummaryViewModel(payload));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && summary === null) {
    return <div className="page-state">正在读取家庭财务摘要…</div>;
  }

  if (error && summary === null) {
    return (
      <div className="page-state page-state--error">
        <p>财务摘要加载失败：{error}</p>
        <Button onClick={() => void load()}>重新加载</Button>
      </div>
    );
  }

  if (summary === null) {
    return <div className="page-state">当前没有可展示的财务摘要。</div>;
  }

  const hero = summary.hero;
  const heroTone = hero.netCashFlowMinor < 0 ? "financial-hero__amount--negative" : "";

  return (
    <div className="workspace-stack">
      <section className="financial-hero" aria-labelledby="financial-hero-title">
        <div className="financial-hero__copy">
          <p id="financial-hero-title" className="eyebrow">
            展示期净现金流
          </p>
          <p className={`financial-hero__amount ${heroTone}`.trim()}>{hero.netCashFlowText}</p>
          <p className="financial-hero__note">
            基于后端标记为 show 的 {hero.monthCount} 个自然月；消费侧覆盖完整性不等同于收入来源已经穷尽。
          </p>
        </div>
        <dl className="financial-hero__breakdown">
          <div>
            <dt>收入</dt>
            <dd>{hero.totalIncomeText}</dd>
          </div>
          <div>
            <dt>净消费</dt>
            <dd>{hero.totalSpendingText}</dd>
          </div>
        </dl>
      </section>

      <section className="section-block" aria-labelledby="months-title">
        <div className="section-heading">
          <div>
            <h2 id="months-title">近期月份</h2>
            <p>沿用后端月份展示策略，不在前端重新判断完整性。</p>
          </div>
          {error ? (
            <Button variant="ghost" onClick={() => void load()}>
              刷新失败，重试
            </Button>
          ) : null}
        </div>

        {summary.visibleMonths.length === 0 ? (
          <div className="empty-state">暂无可展示的完整月份。</div>
        ) : (
          <div className="month-list">
            <div className="month-row month-row--header" aria-hidden="true">
              <span>月份</span>
              <span>收入</span>
              <span>净消费</span>
              <span>净现金流</span>
            </div>
            {summary.visibleMonths.slice(0, 6).map((month) => (
              <div className="month-row" key={month.month}>
                <span className="month-row__name">{month.month}</span>
                <span>{month.totalIncomeText}</span>
                <span>{month.totalSpendingText}</span>
                <span className="month-row__cash-flow">{month.netCashFlowText}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
