"use client";

import { useEffect, useMemo, useRef } from "react";
import cytoscape from "cytoscape";
// @ts-expect-error — cytoscape-cose-bilkent has no types
import coseBilkent from "cytoscape-cose-bilkent";
import type { Core, ElementDefinition } from "cytoscape";
import {
  INTENT_COLOR,
  Intent,
  type ClusterRecord,
  type MemoryEdge,
} from "@/lib/types";

// Register layout once on the client only.
if (typeof window !== "undefined") {
  try {
    cytoscape.use(coseBilkent);
  } catch {
    /* already registered */
  }
}

// Drives cytoscape core directly (no react-cytoscapejs — its prop-diffing
// breaks on updates with "TypeError: n is not a function").
const STYLESHEET = [
  {
    selector: "node",
    style: {
      "background-color": "data(color)",
      label: "data(label)",
      width: "data(size)",
      height: "data(size)",
      "font-size": 9,
      color: "#CDD0D7",
      "text-wrap": "wrap",
      "text-max-width": "120px",
      "text-valign": "bottom",
      "text-margin-y": 4,
      "border-width": 1,
      "border-color": "#0E1014",
      "overlay-opacity": 0,
    },
  },
  {
    selector: "node.highlight",
    style: {
      "border-width": 4,
      "border-color": "#E9A83D",
      "z-index": 999,
    },
  },
  {
    selector: "node.faded",
    style: { opacity: 0.15 },
  },
  {
    selector: "edge",
    style: {
      width: "data(width)",
      "line-color": "#39404F",
      "target-arrow-color": "#39404F",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      label: "data(relation)",
      "font-size": 7,
      color: "#959CAA",
      "text-rotation": "autorotate",
      "text-background-color": "#161922",
      "text-background-opacity": 0.85,
      "text-background-padding": "2px",
      "text-background-shape": "roundrectangle",
    },
  },
  {
    selector: "edge.faded",
    style: { opacity: 0.08 },
  },
] as unknown as cytoscape.StylesheetJson;

const LAYOUT = {
  name: "cose-bilkent",
  animate: false,
  nodeRepulsion: 4500,
  idealEdgeLength: 110,
  edgeElasticity: 0.45,
  gravity: 0.25,
  numIter: 2500,
  tile: true,
  randomize: true,
} as unknown as cytoscape.LayoutOptions;

export interface CytoscapeGraphProps {
  nodes: ClusterRecord[];
  edges: MemoryEdge[];
  highlight?: string | null;
  onNodeClick?: (clusterId: string) => void;
}

export function CytoscapeGraph({
  nodes,
  edges,
  highlight,
  onNodeClick,
}: CytoscapeGraphProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const lastElementsJson = useRef<string>("");
  const onNodeClickRef = useRef(onNodeClick);
  onNodeClickRef.current = onNodeClick;

  const elements = useMemo<ElementDefinition[]>(() => {
    const maxFreq = Math.max(1, ...nodes.map((n) => n.frequency));
    const nodeElements: ElementDefinition[] = nodes.map((n) => {
      const primary = n.dominant_intents[0] ?? Intent.OTHER;
      const size = 24 + 56 * (Math.log10(1 + n.frequency) / Math.log10(1 + maxFreq));
      return {
        data: {
          id: n.id,
          label: n.canonical_question ?? n.label ?? n.id.slice(0, 6),
          frequency: n.frequency,
          intent: primary,
          color: INTENT_COLOR[primary],
          size,
        },
      };
    });

    const edgeElements: ElementDefinition[] = edges.map((e, i) => ({
      data: {
        id: e.id ?? `e-${i}`,
        source: e.source_cluster_id,
        target: e.target_cluster_id,
        relation: e.relation,
        weight: e.weight,
        width: 0.5 + e.weight * 6,
      },
    }));
    return [...nodeElements, ...edgeElements];
  }, [nodes, edges]);

  // Create the cytoscape instance once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const cy = cytoscape({
      container,
      style: STYLESHEET,
      minZoom: 0.1,
      maxZoom: 3,
    });
    cy.on("tap", "node", (evt) => {
      onNodeClickRef.current?.(evt.target.id());
    });
    cyRef.current = cy;
    // A fresh instance has no elements regardless of what the previous
    // instance was showing (StrictMode remounts) — force the next sync.
    lastElementsJson.current = "";
    return () => {
      cyRef.current = null;
      cy.destroy();
    };
  }, []);

  // Sync elements; re-layout only when the data actually changed (SWR
  // revalidation hands us fresh-but-identical arrays).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const json = JSON.stringify(elements);
    if (json === lastElementsJson.current) return;
    lastElementsJson.current = json;
    cy.batch(() => {
      cy.elements().remove();
      cy.add(elements);
    });
    cy.layout(LAYOUT).run();
  }, [elements]);

  // Highlight handling
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("highlight").removeClass("faded");
      if (!highlight) return;
      const q = highlight.toLowerCase();
      const matched = cy.nodes().filter((n) => {
        const label = String(n.data("label") ?? "").toLowerCase();
        return label.includes(q) || String(n.id()).toLowerCase().includes(q);
      });
      if (matched.length === 0) return;
      cy.elements().addClass("faded");
      matched.removeClass("faded").addClass("highlight");
      const neighborhood = matched.closedNeighborhood();
      neighborhood.removeClass("faded");
    });
  }, [highlight, elements]);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
}

export default CytoscapeGraph;
