import {
  toFinancialSummaryViewModel,
  toFinancialTrendViewModel,
  toSpendingAnalyticsViewModel,
  type FinancialSummaryViewModel,
  type FinancialTrendViewModel,
  type SpendingStatistics,
} from "@family-spending/core";
import { useCallback, useEffect, useMemo, useState } from "react";

import { familySpendingService } from "../api/client";
import { Button } from "../components/ui/Button";
import "./overview-analytics.css";
import "./spending-overview.css";

/** Read-first Overview: financial and spending semantics come from generated backend projections. */
export function SpendingOverviewPage() {
  const [summary, setSummary] = useState<FinancialSummaryViewModel | null>(null);
  const [trend, setTrend] = useState<FinancialTrendViewModel | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [spendingStatistics, setSpendingStatistics] = useState<SpendingStatistics | null>(null);
  const [selectedSpendingMonth, setSelectedSpendingMonth] = useState<string | null>(null);
  const [spendingLoading, setSpendingLoading] = useState(true);
  const [spendingError, setSpendingError] = useState<string | null>(null);

  const loadFinancialSummary = useCallback(async () => {
    setSummaryLoading(true);
    setSummaryError(null);
    try {
      const payload = await familySpendingService.getFinancialSummary();
      setSummary(toFinancialSummaryViewModel(payload));
      setTrend(toFinancialTrendViewModel(payload));
    } catch (caught) {
      setSummaryError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSummaryLoading(false);
    }
  }, []);

  const loadSpendingStatistics = useCallback(async () => {
    setSpendingLoading(true);
    setSpendingError(null);
    try {
      const payload = await familySpendingService.getSpendingStatistics();
      setSpendingStatistics(payload);
      setSelectedSpendingMonth((current) =>
        toSpendingAnalyticsViewModel(payload, current ?? undefined).selectedMonth,
      );
    } catch (caught) {
      setSpendingError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSpendingLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadFinancialSummary();
    void loadSpendingStatistics();
  }, [loadFinancialSummary, loadSpendingStatistics]);

  const spending = useMemo(
    () =>
      spendingStatistics === null
        ? null
        : toSpendingAnalyticsViewModel(
            spendingStatistics,
            selectedSpendingMonth ?? undefined,
          ),
    [selectedSpendingMonth, spendingStatistics],
  );

  if (summaryLoading && summary === null) {
    return <div className="page-state">正在读取家庭财务摘要…</div>;
  }

  if (summaryError && summary === null) {
    return (
      <div className="page-state page-state--error">
        <p>财务摘要加载失败：{summaryError}</p>
        <Button onClick={() => void loadFinancialSummary()}>重新加载</Button>
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

      <section className="section-block overview-trend" aria-labelledby="trend-title">
        <div className="section-heading">
          <div>
            <h2 id="trend-title">收支趋势</h2>
            <p>最近最多 12 个后端 show=true 的自然月；这里只改变展示，不重新判断月份完整性。</p>
          </div>
          {trend && trend.points.length > 0 ? (
            <span className="overview-trend__scale">最高月度规模 {trend.maxAmountText}</span>
          ) : null}
        </div>

        {!trend || trend.points.length === 0 ? (
          <div className="empty-state">暂无可绘制的完整月份。</div>
        ) : (
          <>
            <div className="overview-trend__legend" aria-hidden="true">
              <span><i className="overview-trend__swatch overview-trend__swatch--income" />收入</span>
              <span><i className="overview-trend__swatch overview-trend__swatch--spending" />净消费</span>
            </div>
            <div
              className="overview-trend__chart"
              role="img"
              aria-label={`最近 ${trend.points.length} 个展示月的收入与净消费趋势`}
            >
              {trend.points.map((point) => (
                <div className="overview-trend__point" key={point.month}>
                  <div className="overview-trend__bars">
                    <span
                      className="overview-trend__bar overview-trend__bar--income"
                      style={{ height: `${point.incomeHeightPercent}%` }}
                      title={`${point.month} 收入 ${point.totalIncomeText}`}
                    />
                    <span
                      className="overview-trend__bar overview-trend__bar--spending"
                      style={{ height: `${point.spendingHeightPercent}%` }}
                      title={`${point.month} 净消费 ${point.totalSpendingText}`}
                    />
                  </div>
                  <span className="overview-trend__month">{point.month}</span>
                  <span className={`overview-trend__net${point.netCashFlowMinor < 0 ? " overview-trend__net--negative" : ""}`}>
                    净 {point.netCashFlowText}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      <section className="section-block spending-analytics" aria-labelledby="spending-analytics-title">
        <div className="section-heading spending-analytics__heading">
          <div>
            <h2 id="spending-analytics-title">消费结构</h2>
            <p>直接读取后端已聚合的月份 × Category / Merchant；前端只负责选择月份和展示占比。</p>
          </div>
          <div className="spending-analytics__controls">
            {spendingLoading && spendingStatistics !== null ? (
              <span className="spending-analytics__refreshing">刷新中…</span>
            ) : null}
            {spendingError && spendingStatistics !== null ? (
              <Button variant="ghost" onClick={() => void loadSpendingStatistics()}>
                刷新失败，重试
              </Button>
            ) : null}
            {spending && spending.monthOptions.length > 0 ? (
              <label className="spending-analytics__month-control">
                <span>月份</span>
                <select
                  value={spending.selectedMonth ?? ""}
                  onChange={(event) => setSelectedSpendingMonth(event.target.value)}
                >
                  {spending.monthOptions.map((option) => (
                    <option value={option.month} key={option.month}>
                      {option.month} · {option.totalSpendingText}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
        </div>

        {spendingLoading && spendingStatistics === null ? (
          <div className="spending-analytics__state">正在读取消费结构…</div>
        ) : spendingError && spendingStatistics === null ? (
          <div className="spending-analytics__state spending-analytics__state--error">
            <p>消费结构加载失败：{spendingError}</p>
            <Button onClick={() => void loadSpendingStatistics()}>重新加载</Button>
          </div>
        ) : !spending || spending.selectedMonth === null ? (
          <div className="empty-state">暂无后端标记为 show=true 的消费月份。</div>
        ) : (
          <>
            <div className="spending-analytics__snapshot">
              <div>
                <span>当月净消费</span>
                <strong>{spending.totalSpendingText}</strong>
              </div>
              <div>
                <span>消费笔数</span>
                <strong>{spending.transactionCount} 笔</strong>
              </div>
              <div>
                <span>消费侧覆盖</span>
                <strong>{spending.isComplete ? "完整" : "不完整"}</strong>
              </div>
            </div>

            <div className="spending-analytics__grid">
              <article className="spending-analytics__panel" aria-labelledby="category-ranking-title">
                <div className="spending-analytics__panel-heading">
                  <div>
                    <p className="eyebrow">Category</p>
                    <h3 id="category-ranking-title">消费构成</h3>
                  </div>
                  <span>{spending.categories.length} 类</span>
                </div>
                <div className="spending-analytics__ranking">
                  {spending.categories.map((item) => (
                    <div className="spending-analytics__rank-row" key={item.category}>
                      <span className="spending-analytics__rank">{item.rank}</span>
                      <div className="spending-analytics__rank-main">
                        <div className="spending-analytics__rank-copy">
                          <strong>{item.category}</strong>
                          {item.isUnclassified ? <span className="spending-analytics__badge">待复核</span> : null}
                          <small>{item.transactionCount} 笔 · {item.shareText}</small>
                        </div>
                        <div className="spending-analytics__bar" aria-hidden="true">
                          <span style={{ width: `${item.sharePercent}%` }} />
                        </div>
                      </div>
                      <strong className="spending-analytics__amount">{item.spendingText}</strong>
                    </div>
                  ))}
                </div>
              </article>

              <article className="spending-analytics__panel" aria-labelledby="merchant-ranking-title">
                <div className="spending-analytics__panel-heading">
                  <div>
                    <p className="eyebrow">Merchant</p>
                    <h3 id="merchant-ranking-title">Top Merchant</h3>
                  </div>
                  <span>前 {spending.topMerchants.length} 项</span>
                </div>
                <div className="spending-analytics__merchant-list">
                  {spending.topMerchants.map((item) => (
                    <div className="spending-analytics__merchant-row" key={`${item.rank}-${item.displayName}`}>
                      <span className="spending-analytics__rank">{item.rank}</span>
                      <div className="spending-analytics__merchant-copy">
                        <strong>{item.displayName}</strong>
                        <small>
                          {item.transactionCount} 笔 · {item.shareText}
                          {item.isUnclassified ? " · 待分类" : ""}
                        </small>
                      </div>
                      <strong className="spending-analytics__amount">{item.spendingText}</strong>
                    </div>
                  ))}
                </div>
              </article>
            </div>
          </>
        )}
      </section>

      <section className="section-block" aria-labelledby="months-title">
        <div className="section-heading">
          <div>
            <h2 id="months-title">近期月份</h2>
            <p>沿用后端月份展示策略，不在前端重新判断完整性。</p>
          </div>
          {summaryError ? (
            <Button variant="ghost" onClick={() => void loadFinancialSummary()}>
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
