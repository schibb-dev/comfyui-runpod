import React from "react";

export function VideoAutoplayToggle({
  videoAutoplay,
  onVideoAutoplayChange,
  label,
  className,
}: {
  videoAutoplay: boolean;
  onVideoAutoplayChange: (on: boolean) => void;
  label: string;
  className?: string;
}) {
  return (
    <label className={className}>
      <input
        type="checkbox"
        checked={videoAutoplay}
        onChange={(e) => onVideoAutoplayChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}
