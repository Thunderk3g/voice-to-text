"use client";

import { useEffect, useMemo, useRef } from "react";
import dynamic from "next/dynamic";
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

// react-cytoscapejs must be dynamically imported with ssr:false.
const CytoscapeComponent = dynamic(() => import("react-cytoscapejs"), {
  ssr: false,
}) as unknown as React.ComponentType<{
  elements: ElementDefinition[];
  layout: cytoscape.LayoutOptions;
  style: React.CSSProperties;
  stylesheet: unknown[];
  cy?: (cy: Core) => void;
  minZoom?: number;
  maxZoom?: number;
}>;

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
  const cyRef = useRef<Core | null>(null);

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

  const stylesheet = useMemo<unknown[]>(
    () => [
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
    ],
    [],
  );

  const layout = useMemo<cytoscape.LayoutOptions>(
    () =>
      ({
        name: "cose-bilkent",
        animate: false,
        nodeRepulsion: 4500,
        idealEdgeLength: 110,
        edgeElasticity: 0.45,
        gravity: 0.25,
        numIter: 2500,
        tile: true,
        randomize: true,
      }) as unknown as cytoscape.LayoutOptions,
    [],
  );

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

  return (
    <CytoscapeComponent
      elements={elements}
      layout={layout}
      stylesheet={stylesheet}
      style={{ width: "100%", height: "100%" }}
      minZoom={0.1}
      maxZoom={3}
      cy={(cy) => {
        cyRef.current = cy;
        cy.removeListener("tap", "node");
        cy.on("tap", "node", (evt) => {
          const id = evt.target.id();
          if (onNodeClick) onNodeClick(id);
        });
      }}
    />
  );
}

export default CytoscapeGraph;
