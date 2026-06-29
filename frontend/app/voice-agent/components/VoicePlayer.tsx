"use client";

import { useRef, useState, useEffect } from "react";
import clsx from "clsx";
import { Play, Pause, Volume2, Bot, User, HelpCircle, AlertCircle } from "lucide-react";
import useSWR from "swr";
import { type UtteranceSchema, type TranscriptionResponse } from "@/lib/types";
import { Card } from "@/components/Card";
import { LoadingBlock } from "@/components/Spinner";
import { LanguageBadge } from "@/components/LanguageBadge";
import { WaveformVisualization } from "./WaveformVisualization";

interface VoicePlayerProps {
  callId: string;
  utterances?: UtteranceSchema[];
}

export function VoicePlayer({
  callId,
  utterances,
}: VoicePlayerProps): JSX.Element {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);

  // Fetch transcription which includes audio_url
  const { data: transcription, error: transcriptionError } = useSWR<TranscriptionResponse>(
    callId ? `/calls/${callId}/transcription` : null,
  );

  const audioUrl = transcription?.audio_url || "";

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const updateTime = () => setCurrentTime(audio.currentTime);
    const updateDuration = () => setDuration(audio.duration);
    const handleEnded = () => setIsPlaying(false);

    audio.addEventListener("timeupdate", updateTime);
    audio.addEventListener("loadedmetadata", updateDuration);
    audio.addEventListener("ended", handleEnded);

    return () => {
      audio.removeEventListener("timeupdate", updateTime);
      audio.removeEventListener("loadedmetadata", updateDuration);
      audio.removeEventListener("ended", handleEnded);
    };
  }, []);

  function togglePlayPause(): void {
    if (audioRef.current) {
      if (isPlaying) {
        audioRef.current.pause();
      } else {
        audioRef.current.play().catch(() => {
          /* audio may not be available yet */
        });
      }
      setIsPlaying(!isPlaying);
    }
  }

  function formatTime(seconds: number): string {
    if (!isFinite(seconds)) return "0:00";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  }

  // Determine which utterance is currently playing.
  const currentUtterance = utterances?.find(
    (u) => currentTime >= u.start_ts && currentTime < u.end_ts,
  );

  // Handle loading state
  if (!transcription && !transcriptionError) {
    return (
      <div className="flex flex-col gap-4">
        <Card>
          <LoadingBlock label="Loading audio..." />
        </Card>
      </div>
    );
  }

  // Handle error state
  if (transcriptionError || !audioUrl) {
    return (
      <div className="flex flex-col gap-4">
        <Card>
          <div className="flex items-start gap-3">
            <AlertCircle className="h-4 w-4 text-warn-400 mt-0.5 shrink-0" />
            <p className="text-sm text-ink-500">
              {transcriptionError
                ? `Failed to load audio: ${transcriptionError.message}`
                : "Audio file is not available for this call."}
            </p>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Audio Player Card */}
      <Card>
        <div className="flex flex-col gap-4">
          {/* Player Controls */}
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={togglePlayPause}
              className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-500 text-[#1D1407] transition hover:bg-brand-600 active:bg-brand-400"
              aria-label={isPlaying ? "Pause" : "Play"}
            >
              {isPlaying ? (
                <Pause className="h-5 w-5" />
              ) : (
                <Play className="h-5 w-5" />
              )}
            </button>

            {/* Time Display */}
            <span className="font-mono text-[10px] text-ink-400 shrink-0 w-8">
              {formatTime(currentTime)}
            </span>

            {/* Waveform */}
            <div className="flex-1 min-w-0">
              <WaveformVisualization
                currentTime={currentTime}
                duration={duration}
                flaggedSegments={[]}
                onSeek={(time) => {
                  if (audioRef.current) {
                    audioRef.current.currentTime = time;
                    setCurrentTime(time);
                  }
                }}
              />
            </div>

            {/* Duration */}
            <span className="font-mono text-[10px] text-ink-400 shrink-0 w-8">
              {formatTime(duration)}
            </span>

            {/* Volume Control */}
            <div className="flex items-center gap-2">
              <Volume2 className="h-4 w-4 text-ink-400" />
              <input
                type="range"
                min="0"
                max="1"
                step="0.1"
                value={volume}
                onChange={(e) => {
                  const val = parseFloat(e.target.value);
                  setVolume(val);
                  if (audioRef.current) {
                    audioRef.current.volume = val;
                  }
                }}
                className="w-12 h-1 cursor-pointer appearance-none rounded-full bg-ink-200 accent-brand-500"
              />
            </div>
          </div>

          {/* Hidden Audio Element */}
          <audio
            ref={audioRef}
            src={audioUrl}
            crossOrigin="anonymous"
            onLoadedMetadata={() => {
              setDuration(audioRef.current?.duration ?? 0);
            }}
          />
        </div>
      </Card>

      {/* Transcript Display */}
      <Card title="Transcript">
        {!utterances && <LoadingBlock />}
        {utterances && utterances.length === 0 && (
          <div className="text-center text-sm text-ink-400">
            No transcript available yet.
          </div>
        )}
        {utterances && utterances.length > 0 && (
          <div className="flex flex-col gap-2.5 max-h-96 overflow-y-auto">
            {utterances.map((u, i) => (
              <UtteranceDisplay
                key={u.id ?? `${i}-${u.start_ts}`}
                u={u}
                isCurrent={currentUtterance?.id === u.id}
                onSeek={(ts) => {
                  if (audioRef.current) {
                    audioRef.current.currentTime = ts;
                    setCurrentTime(ts);
                  }
                }}
              />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

interface UtteranceDisplayProps {
  u: UtteranceSchema;
  isCurrent: boolean;
  onSeek: (time: number) => void;
}

function UtteranceDisplay({
  u,
  isCurrent,
  onSeek,
}: UtteranceDisplayProps): JSX.Element {
  const isAgent = u.speaker === "AGENT";
  const isCustomer = u.speaker === "CUSTOMER";

  function formatTime(s: number): string {
    const mins = Math.floor(s / 60);
    const secs = Math.floor(s % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  }

  return (
    <button
      type="button"
      onClick={() => onSeek(u.start_ts)}
      className={clsx(
        "flex w-full gap-2 rounded-lg border px-3 py-2 text-left transition-all",
        isCurrent
          ? "border-brand-500/60 bg-brand-500/10"
          : "border-ink-200/40 hover:border-ink-200/60 hover:bg-ink-100/30",
      )}
    >
      <div className="shrink-0">
        {isAgent ? (
          <Bot className="h-4 w-4 text-jade-500 mt-0.5" />
        ) : isCustomer ? (
          <User className="h-4 w-4 text-brand-500 mt-0.5" />
        ) : (
          <HelpCircle className="h-4 w-4 text-ink-400 mt-0.5" />
        )}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-ink-500">
            {u.speaker.toLowerCase()}
            {u.speaker_id ? ` ${u.speaker_id}` : ""}
          </span>
          <span className="font-mono text-[9px] text-ink-400">
            {formatTime(u.start_ts)}
          </span>
          <LanguageBadge language={u.language} />
          <span className="text-[9px] text-ink-400">
            {(u.confidence * 100).toFixed(0)}%
          </span>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-ink-700 break-words">
          {u.text}
        </p>
      </div>
    </button>
  );
}

export default VoicePlayer;
