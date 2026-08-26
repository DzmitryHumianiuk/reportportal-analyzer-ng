import { useEffect, useRef, useState } from 'react';

import { echartsBase } from '../lib/echartsTheme';

/** The slice of the ECharts instance API this app uses. */
export interface ChartHandle {
  setOption(option: object, opts?: object): void;
  resize(): void;
  dispose(): void;
  getDom(): HTMLElement;
}

export interface EChartProps {
  option: object;
  height: number | string;
  /** 3D charts (scatter3D): loads echarts-gl before init. */
  useGl?: boolean;
  className?: string;
  onInit?: (chart: unknown) => void;
}

/**
 * ECharts host. The library is imported dynamically so the 700 kB chart bundle
 * stays out of the initial page and so tests can substitute a double before the
 * first chart mounts.
 */
export function EChart({ option, height, useGl, className, onInit }: EChartProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [chart, setChart] = useState<ChartHandle | null>(null);
  const onInitRef = useRef(onInit);
  onInitRef.current = onInit;

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return undefined;
    let cancelled = false;
    let instance: ChartHandle | null = null;
    let observer: ResizeObserver | null = null;

    void (async () => {
      const echarts = await import('echarts');
      // echarts-gl registers its series types into echarts, so it must load
      // after echarts and before init().
      if (useGl) await import('echarts-gl');
      if (cancelled || !hostRef.current) return;
      instance = echarts.init(el) as unknown as ChartHandle;
      observer = new ResizeObserver(() => instance?.resize());
      observer.observe(el);
      setChart(instance);
      onInitRef.current?.(instance);
    })();

    return () => {
      cancelled = true;
      observer?.disconnect();
      instance?.dispose();
      setChart(null);
    };
  }, [useGl]);

  useEffect(() => {
    if (!chart) return;
    chart.setOption({ ...echartsBase(), ...option }, { notMerge: true });
  }, [chart, option]);

  return (
    <div
      ref={hostRef}
      className={className ? `chart ${className}` : 'chart'}
      style={{ height: typeof height === 'number' ? `${height}px` : height }}
    />
  );
}
