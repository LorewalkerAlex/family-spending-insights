import {
  toFinancialSummaryViewModel,
  toSpendingAnalyticsViewModel,
  type FinancialMonthViewModel,
  type FinancialSummaryViewModel,
  type SpendingStatistics,
} from "@family-spending/core";
import { useCallback, useEffect, useMemo, useState } from "react";

import { familySpendingService } from "../api/client";
import { Button } from "../components/ui/Button";
import "./spending-overview.css";

const TREND_WIDTH = 900;
const TREND_HEIGHT = 250;
const TREND_BASELINE = 218;
const TREND_TOP = 26;
const TREND_HORIZONTAL_PADDING = 22;

function formatMonthLabel(month: string): string {
  const [year, monthNumber] = month.split("-");
  return `${year} 年 ${Number(monthNumber)} 月`;
}

function latestMonth(months: readonly FinancialMonthViewModel[]): string | null {
  return months.length === 0
    ? null
    : months.reduce((latest, month) => (month.month > latest ? month.month : latest), months[0]!.month);
}

interface TrendPoint {
  month: FinancialMonthViewModel;
  x: number;
  y: number;
}

/** Convert backend-authoritative monthly spending totals into deterministic SVG coordinates. */
function buildTrendPoints(
  months: readonly FinancialMonthViewModel[],
  maxAmount: number,
): readonly TrendPoint[] {
  const usableHeight = TREND_BASELINE - TREND_TOP;
  const usableWidth = TREND_WIDTH - TREND_HORIZONTAL_PADDING * 2;
  const denominator = Math.max(1, months.length - 1);
  return months.map((month, index) => ({
    month,
    x:
      months.length === 1
        ? TREND_WIDTH / 2
        : TREND_HORIZONTAL_PADDING + (index / denominator) * usableWidth,
    y:
      TREND_BASELINE -
      Math.max(0, Math.min(1, month.totalSpendingMinor / Math.max(1, maxAmount))) * usableHeight,
  }));
}

function linePath(points: readonly TrendPoint[]): string {
  if (points.length === 0) return "";
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
}

function areaPath(points: readonly TrendPoint[]): string {
  if (points.length === 0) return "";
  return `M ${points[0]!.x} ${TREND_BASELINE} ${linePath(points).replace(/^M /, "L ")} L ${points.at(-1)!.x} ${TREND_BASELINE} Z`;
}

/** Read-first Overview: backend projections own finance semantics; this page only composes presentation. */
export function SpendingOverviewPage() {
  const [summary, setSummary] = useState<FinancialSummaryViewModel | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [spendingStatistics, setSpendingStatistics] = useState<SpendingStatistics | null>(null);
  const [selectedMonth, setSelectedMonth] = useState<string | null>(null);
  const [spendingLoading, setSpendingLoading] = useState(true);
  const [spendingError, setSpendingError] = useState<string | null>(null);

  const loadFinancialSummary = useCallback(async () => {
    setSummaryLoading(true);
    setSummaryError(null);
    try {
      const payload = await familySpendingService.getFinancialSummary();
      const nextSummary = toFinancialSummaryViewModel(payload);
      setSummary(nextSummary);
      setSelectedMonth((current) => current ?? latestMonth(nextSummary.visibleMonths));
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

  const monthOptions = useMemo(
    () =>
      summary?.visibleMonths
        .slice()
        .sort((left, right) => right.month.localeCompare(left.month)) ?? [],
    [summary],
  );

  const activeMonth = selectedMonth ?? monthOptions[0]?.month ?? null;
  const activeFinancialMonth = useMemo(
    () => monthOptions.find((month) => month.month === activeMonth) ?? monthOptions[0] ?? null,
    [activeMonth, monthOptions],
  );
  const trendMonths = useMemo(
    () => monthOptions.slice().sort((left, right) => left.month.localeCompare(right.month)).slice(-12),
    [monthOptions],
  );
  const maxTrendSpendingMinor = Math.max(1, ...trendMonths.map((month) => month.totalSpendingMinor));
  const maxTrendMonth = trendMonths.find((month) => month.totalSpendingMinor === maxTrendSpendingMinor) ?? null;
  const trendPoints = useMemo(
    () => buildTrendPoints(trendMonths, maxTrendSpendingMinor),
    [maxTrendSpendingMinor, trendMonths],
  );

  const spending = useMemo(
    () =>
      spendingStatistics === null
        ? null
        : toSpendingAnalyticsViewModel(spendingStatistics, activeMonth ?? undefined),
    [activeMonth, spendingStatistics],
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

  if (summary === null || activeFinancialMonth === null) {
    return <div className="page-state">当前没有可展示的财务摘要。</div>;
  }

  const cashFlowTone =
    activeFinancialMonth.netCashFlowMinor < 0 ? "overview-hero__amount--negative" : "";
  const visibleCategories = spending?.categories.slice(0, 6) ?? [];
  const visibleMerchants = spending?.topMerchants.slice(0, 6) ?? [];
  const activeTrendPoint = trendPoints.find((point) => point.month.month === activeFinancialMonth.month) ?? null;

  return (
    <div className="overview-premium">
      <section className="overview-premium__masthead" aria-label="月份选择">
        <div>
          <span className="overview-premium__context"><i />家庭现金流</span>
          <h2>{formatMonthLabel(activeFinancialMonth.month)}</h2>
        </div>
        <label className="overview-premium__month-select">
          <span className="sr-only">切换月份</span>
          <select
            value={activeFinancialMonth.month}
            onChange={(event) => setSelectedMonth(event.target.value)}
          >
            {monthOptions.map((month) => (
              <option value={month.month} key={month.month}>
                {formatMonthLabel(month.month)}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section className="overview-hero" aria-labelledby="overview-hero-title">
        <div className="overview-hero__ambient" aria-hidden="true" />
        <div className="overview-hero__primary">
          <span id="overview-hero-title">本月结余</span>
          <strong className={`overview-hero__amount ${cashFlowTone}`.trim()}>
            {activeFinancialMonth.netCashFlowText}
          </strong>
          <p>收入扣除净消费后的月度现金流。</p>
        </div>

        <dl className="overview-hero__metrics">
          <div>
            <dt>收入</dt>
            <dd>{activeFinancialMonth.totalIncomeText}</dd>
            <span>流入</span>
          </div>
          <div>
            <dt>净消费</dt>
            <dd>{activeFinancialMonth.totalSpendingText}</dd>
            <span>流出</span>
          </div>
          <div>
            <dt>消费笔数</dt>
            <dd>{spending?.selectedMonth === activeFinancialMonth.month ? spending.transactionCount : "—"}</dd>
            <span>记录</span>
          </div>
        </dl>
      </section>

      <section className="overview-premium__section" aria-labelledby="overview-trend-title">
        <header className="overview-premium__section-heading">
          <div>
            <span>消费走势</span>
            <h2 id="overview-trend-title">最近 {trendMonths.length} 个完整月份</h2>
          </div>
          <div className="overview-premium__section-aside">
            <strong>{activeFinancialMonth.totalSpendingText}</strong>
            <span>{formatMonthLabel(activeFinancialMonth.month)} 净消费</span>
          </div>
        </header>

        {trendMonths.length === 0 ? (
          <div className="empty-state">暂无可绘制的完整月份。</div>
        ) : (
          <article className="overview-trend-surface">
            <div className="overview-trend-surface__meta">
              <span>最高支出月</span>
              <strong>{maxTrendMonth ? `${formatMonthLabel(maxTrendMonth.month)} · ${maxTrendMonth.totalSpendingText}` : "—"}</strong>
            </div>

            <div className="overview-trend-plot">
              <svg
                viewBox={`0 0 ${TREND_WIDTH} ${TREND_HEIGHT}`}
                preserveAspectRatio="none"
                role="img"
                aria-label={`最近 ${trendMonths.length} 个完整月份的净消费趋势`}
              >
                <line className="overview-trend-plot__grid" x1="0" y1="74" x2={TREND_WIDTH} y2="74" />
                <line className="overview-trend-plot__grid" x1="0" y1="146" x2={TREND_WIDTH} y2="146" />
                <line className="overview-trend-plot__baseline" x1="0" y1={TREND_BASELINE} x2={TREND_WIDTH} y2={TREND_BASELINE} />
                <path className="overview-trend-plot__area" d={areaPath(trendPoints)} />
                <path className="overview-trend-plot__line" d={linePath(trendPoints)} />
                {trendPoints.map((point) => {
                  const isActive = point.month.month === activeFinancialMonth.month;
                  return (
                    <g key={point.month.month}>
                      <circle
                        className={`overview-trend-plot__point${isActive ? " overview-trend-plot__point--active" : ""}`}
                        cx={point.x}
                        cy={point.y}
                        r={isActive ? 6 : 3.5}
                      />
                    </g>
                  );
                })}
              </svg>
              {activeTrendPoint ? (
                <div
                  className="overview-trend-plot__focus"
                  style={{
                    left: `${(activeTrendPoint.x / TREND_WIDTH) * 100}%`,
                    top: `${(activeTrendPoint.y / TREND_HEIGHT) * 100}%`,
                  }}
                  aria-hidden="true"
                >
                  <span>{activeFinancialMonth.totalSpendingText}</span>
                </div>
              ) : null}
            </div>

            <div className="overview-trend-months" aria-label="选择趋势月份">
              {trendMonths.map((month) => (
                <button
                  type="button"
                  className={month.month === activeFinancialMonth.month ? "is-active" : ""}
                  key={month.month}
                  onClick={() => setSelectedMonth(month.month)}
                  aria-label={`${formatMonthLabel(month.month)}，净消费 ${month.totalSpendingText}`}
                >
                  {month.month.slice(5)}
                </button>
              ))}
            </div>
          </article>
        )}
      </section>

      <section className="overview-premium__section" aria-labelledby="overview-breakdown-title">
        <header className="overview-premium__section-heading overview-premium__section-heading--breakdown">
          <div>
            <span>消费结构</span>
            <h2 id="overview-breakdown-title">这个月的钱花去了哪里</h2>
          </div>
          {spendingLoading && spendingStatistics !== null ? (
            <span className="overview-premium__loading">正在刷新</span>
          ) : spendingError && spendingStatistics !== null ? (
            <Button variant="ghost" onClick={() => void loadSpendingStatistics()}>
              刷新失败，重试
            </Button>
          ) : null}
        </header>

        {spendingLoading && spendingStatistics === null ? (
          <div className="overview-data-state">正在读取消费结构…</div>
        ) : spendingError && spendingStatistics === null ? (
          <div className="overview-data-state overview-data-state--error">
            <p>消费结构加载失败：{spendingError}</p>
            <Button onClick={() => void loadSpendingStatistics()}>重新加载</Button>
          </div>
        ) : !spending || spending.selectedMonth === null ? (
          <div className="empty-state">暂无可展示的消费月份。</div>
        ) : (
          <div className="overview-breakdown-layout">
            <article className="overview-category-panel">
              <div className="overview-category-panel__headline">
                <div>
                  <span>分类构成</span>
                  <strong>{spending.totalSpendingText}</strong>
                </div>
                <span>{spending.transactionCount} 笔消费</span>
              </div>

              <div className="overview-category-spectrum" aria-label="分类消费占比">
                {spending.categories.map((item, index) => (
                  <span
                    className={`overview-category-spectrum__segment overview-category-spectrum__segment--${Math.min(index, 6)}${item.isUnclassified ? " is-unclassified" : ""}`}
                    style={{ width: `${item.sharePercent}%` }}
                    key={item.category}
                    title={`${item.category} ${item.shareText}`}
                  />
                ))}
              </div>

              <div className="overview-category-list">
                {visibleCategories.map((item, index) => (
                  <div className="overview-category-row" key={item.category}>
                    <span
                      className={`overview-category-row__dot overview-category-row__dot--${Math.min(index, 6)}${item.isUnclassified ? " is-unclassified" : ""}`}
                      aria-hidden="true"
                    />
                    <div className="overview-category-row__name">
                      <strong>{item.category}</strong>
                      <span>{item.transactionCount} 笔 · {item.shareText}</span>
                    </div>
                    {item.isUnclassified ? <span className="overview-review-badge">待复核</span> : null}
                    <strong className="overview-category-row__amount">{item.spendingText}</strong>
                  </div>
                ))}
              </div>
            </article>

            <article className="overview-merchant-panel">
              <div className="overview-merchant-panel__headline">
                <div>
                  <span>主要商户</span>
                  <strong>Top {visibleMerchants.length}</strong>
                </div>
                <span>{spending.isComplete ? "完整月份" : "数据未完整"}</span>
              </div>

              <div className="overview-merchant-list">
                {visibleMerchants.map((item) => (
                  <div className="overview-merchant-row" key={`${item.rank}-${item.displayName}`}>
                    <span className="overview-merchant-row__rank">{String(item.rank).padStart(2, "0")}</span>
                    <div className="overview-merchant-row__copy">
                      <strong>{item.displayName}</strong>
                      <span>
                        {item.transactionCount} 笔 · {item.shareText}
                        {item.isUnclassified ? " · 待分类" : ""}
                      </span>
                    </div>
                    <strong className="overview-merchant-row__amount">{item.spendingText}</strong>
                  </div>
                ))}
              </div>
            </article>
          </div>
        )}
      </section>
    </div>
  );
}
