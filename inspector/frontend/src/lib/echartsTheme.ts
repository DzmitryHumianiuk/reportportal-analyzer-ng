// Shared ECharts options (RP light: dark axis ink, light gridlines). Chart
// tooltips are light (white card + soft shadow) so the rich formatter HTML —
// which colors text with the dark INK/INK2/MUTED tokens — stays readable.

import { HAIRLINE, INK2 } from './colors';

export interface EchartsBaseOption {
  textStyle: { fontFamily: string; color: string };
  grid: { left: number; right: number; top: number; bottom: number; containLabel: boolean };
  tooltip: {
    backgroundColor: string;
    borderColor: string;
    borderWidth: number;
    textStyle: { color: string };
    extraCssText: string;
    confine: boolean;
  };
}

export function echartsBase(): EchartsBaseOption {
  return {
    textStyle: { fontFamily: 'Roboto, Arial, sans-serif', color: INK2 },
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: {
      backgroundColor: '#ffffff',
      borderColor: HAIRLINE,
      borderWidth: 1,
      textStyle: { color: INK2 },
      extraCssText: 'box-shadow:0 8px 40px rgba(0,0,0,.15);border-radius:8px;',
      confine: true,
    },
  };
}
