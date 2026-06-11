import type { PlotParams } from "react-plotly.js";

// Plotly types are re-derived through react-plotly.js so we never import
// "plotly.js" directly (its @types package is a transitive dep and is not
// hoisted under pnpm layouts).
export type PlotData = PlotParams["data"][number];
export type PlotLayout = PlotParams["layout"];

/** Shared Plotly layout tuned for the dark "Marigold Ledger" theme. */
export const DARK_LAYOUT: PlotLayout = {
  margin: { l: 40, r: 16, t: 16, b: 40 },
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: {
    family: "var(--font-body), ui-sans-serif, system-ui",
    size: 11,
    color: "#959CAA",
  },
  showlegend: true,
  legend: { orientation: "h", y: -0.18, font: { color: "#959CAA" } },
  xaxis: { zeroline: false, gridcolor: "#262B38", linecolor: "#39404F" },
  yaxis: { zeroline: false, gridcolor: "#262B38", linecolor: "#39404F" },
  hoverlabel: {
    bgcolor: "#1B1F2A",
    bordercolor: "#39404F",
    font: { color: "#F2EFE7", size: 11 },
  },
};

/** Categorical palette aligned with the dark theme accents. */
export const CHART_COLORS = [
  "#E9A83D", // marigold
  "#3FC096", // jade
  "#7BA7F7",
  "#C89BF2",
  "#F2807B",
  "#5BCBE3",
  "#F79E66",
  "#B59CF5",
];
