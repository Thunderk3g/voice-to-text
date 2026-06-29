"use client";

import { useRef, useEffect, useState } from "react";
import clsx from "clsx";

export interface WaveformProps {
  currentTime: number;
  duration: number;
  flaggedSegments?: Array<{ start: number; end: number }>;
  onSeek?: (time: number) => void;
}

/**
 * 72-bar waveform visualization with flagged segments highlighted.
 * Each bar represents duration/72 seconds.
 */
export function WaveformVisualization({
  currentTime,
  duration,
  flaggedSegments = [],
  onSeek,
}: WaveformProps): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [bars, setBars] = useState<number[]>([]);

  // Generate mock waveform data (in real app, this would come from audio analysis)
  useEffect(() => {
    const barCount = 72;
    const newBars: number[] = [];
    for (let i = 0; i < barCount; i++) {
      // Simulate waveform with some randomness + envelope
      const progress = i / barCount;
      const envelope = Math.sin(progress * Math.PI); // Peaks in middle
      const noise = Math.random() * 0.7 + 0.3;
      newBars.push(noise * envelope);
    }
    setBars(newBars);
  }, []);

  // Draw waveform
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || bars.length === 0) return;

    const ctx = canvas!.getContext("2d");
    if (!ctx) return;

    const barCount = bars.length;
    const barWidth: number = canvas!.width / barCount;
    const centerY: number = canvas!.height / 2;

    ctx.clearRect(0, 0, canvas!.width, canvas!.height);

    // Check if a position is flagged
    const isFlagged = (positionSeconds: number): boolean => {
      return flaggedSegments.some(
        (seg) => positionSeconds >= seg.start && positionSeconds <= seg.end,
      );
    };

    // Draw each bar
    for (let i = 0; i < barCount; i++) {
      const barTime = (i / barCount) * duration;
      const barAmplitude = bars[i] ?? 0;
      const height = barAmplitude * centerY * 0.8;
      const x = i * barWidth;

      const flagged = isFlagged(barTime);
      const played = barTime <= currentTime;

      // Determine color
      let fillColor = "#9BA3AF"; // Default gray
      if (flagged) {
        fillColor = played ? "#F4837F" : "#F4837F80"; // Red for flagged
      } else if (played) {
        fillColor = "#E9A83D"; // Marigold for played
      } else {
        fillColor = "#9BA3AF80"; // Transparent gray for unplayed
      }

      ctx.fillStyle = fillColor;
      ctx.fillRect(x + barWidth * 0.25, centerY - height, barWidth * 0.5, height * 2);
    }

    // Draw current time indicator
    const playheadX = (currentTime / (duration || 1)) * canvas!.width;
    ctx.strokeStyle = "#E9A83D";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(playheadX, 0);
    ctx.lineTo(playheadX, canvas!.height);
    ctx.stroke();
  }, [bars, currentTime, duration, flaggedSegments]);

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current || !onSeek || !duration) return;

    const rect = canvasRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const percent = x / rect.width;
    const seekTime = percent * duration;
    onSeek(seekTime);
  };

  if (duration === 0 || !isFinite(duration)) {
    return (
      <div className="h-12 rounded-lg border border-ink-200 bg-ink-100/50 flex items-center justify-center text-sm text-ink-400">
        No audio available
      </div>
    );
  }

  return (
    <canvas
      ref={canvasRef}
      width={720}
      height={48}
      onClick={handleClick}
      className={clsx(
        "h-12 w-full cursor-pointer rounded-lg border border-ink-200 bg-ink-100/50",
        onSeek && "hover:opacity-80",
      )}
      style={{ display: "block" }}
    />
  );
}

export default WaveformVisualization;
