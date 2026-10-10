import React, { useEffect, useRef } from 'react';
import type { VoiceState } from '../../types/voice';

export interface WaveformProps {
  state?: VoiceState;
  active?: boolean;
  bars?: number;
  className?: string;
}

export function Waveform({ state: rawState, active, bars = 24, className = '' }: WaveformProps) {
  const state: VoiceState = rawState ?? (active ? 'SPEAKING' : 'IDLE');
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animationRef = useRef<number>(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (typeof navigator !== 'undefined' && /jsdom/i.test(navigator.userAgent || '')) {
      return;
    }
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas.getContext('2d');
    } catch {
      ctx = null;
    }
    if (!ctx) return;

    let phase = 0;
    const draw = () => {
      const { width, height } = canvas;
      ctx.clearRect(0, 0, width, height);
      const barWidth = width / bars;
      const centerY = height / 2;
      for (let i = 0; i < bars; i++) {
        let amplitude = 0;
        switch (state) {
          case 'IDLE':
            amplitude = Math.sin(phase + i * 0.3) * 0.1 + 0.1;
            break;
          case 'LISTENING':
            amplitude = Math.abs(Math.sin(phase * 2 + i * 0.5)) * 0.8 + 0.2;
            break;
          case 'PROCESSING':
            amplitude = Math.sin(phase * 3 + i) * 0.3 + 0.5;
            break;
          case 'SPEAKING':
            amplitude = Math.abs(Math.sin(phase * 1.5 + i * 0.4)) * 0.9 + 0.1;
            break;
          case 'INTERRUPTED':
            amplitude = Math.random() * 0.3;
            break;
          case 'ERROR':
            amplitude = 0.05;
            break;
          case 'NOT_CONFIGURED':
            amplitude = 0.02;
            break;
        }
        const barHeight = amplitude * height * 0.8;
        const x = i * barWidth + barWidth * 0.2;
        const y = centerY - barHeight / 2;
        const gradient = ctx.createLinearGradient(0, y, 0, y + barHeight);
        if (state === 'LISTENING') {
          gradient.addColorStop(0, 'rgba(16,185,129,0.8)');
          gradient.addColorStop(1, 'rgba(6,182,212,0.8)');
        } else if (state === 'SPEAKING') {
          gradient.addColorStop(0, 'rgba(59,130,246,0.9)');
          gradient.addColorStop(1, 'rgba(139,92,246,0.9)');
        } else if (state === 'ERROR') {
          gradient.addColorStop(0, 'rgba(239,68,68,0.6)');
          gradient.addColorStop(1, 'rgba(185,28,28,0.6)');
        } else {
          gradient.addColorStop(0, 'rgba(255,255,255,0.3)');
          gradient.addColorStop(1, 'rgba(255,255,255,0.1)');
        }
        ctx.fillStyle = gradient;
        ctx.beginPath();
        if (typeof (ctx as any).roundRect === 'function') {
          (ctx as any).roundRect(x, y, barWidth * 0.6, barHeight, 2);
        } else {
          ctx.rect(x, y, barWidth * 0.6, barHeight);
        }
        ctx.fill();
      }
      phase += 0.05;
      if (state !== 'IDLE' && state !== 'NOT_CONFIGURED' && state !== 'ERROR') {
        animationRef.current = requestAnimationFrame(draw);
      }
    };
    draw();
    return () => {
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
    };
  }, [state, bars]);

  return (
    <div className={`relative ${className}`} role="img" aria-label={`Audio waveform: ${state}`}>
      <canvas ref={canvasRef} width={240} height={60} className="h-16 w-full" aria-hidden="true" />
      <span className="sr-only">Waveform in {state} state</span>
    </div>
  );
}

export default Waveform;
