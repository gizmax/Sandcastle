/**
 * Canvas-based confetti effect - zero dependencies.
 * Uses requestAnimationFrame for smooth 60fps animation.
 */

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  width: number;
  height: number;
  color: string;
  rotation: number;
  rotationSpeed: number;
  opacity: number;
}

const COLORS = [
  "#F59E0B", // amber
  "#FBBF24", // amber-light
  "#3B82F6", // blue
  "#22C55E", // green
  "#A855F7", // purple
  "#EC4899", // pink
  "#F97316", // orange
];

const GRAVITY = 0.12;
const AIR_RESISTANCE = 0.985;
const FADE_START = 2200; // ms - start fading
const DURATION = 3000; // ms - total duration

function createParticle(canvasWidth: number): Particle {
  const angle = -Math.PI / 2 + (Math.random() - 0.5) * 1.2;
  const speed = 6 + Math.random() * 8;
  return {
    x: canvasWidth * (0.3 + Math.random() * 0.4),
    y: window.innerHeight * 0.55,
    vx: Math.cos(angle) * speed + (Math.random() - 0.5) * 4,
    vy: Math.sin(angle) * speed * 1.4 - Math.random() * 3,
    width: 4 + Math.random() * 4,
    height: 6 + Math.random() * 8,
    color: COLORS[Math.floor(Math.random() * COLORS.length)],
    rotation: Math.random() * Math.PI * 2,
    rotationSpeed: (Math.random() - 0.5) * 0.3,
    opacity: 1,
  };
}

/**
 * Fire a confetti burst.
 * @param particleCount - number of confetti pieces (default 100)
 */
export function fireConfetti(particleCount = 100): void {
  // Respect reduced motion preference
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const canvas = document.createElement("canvas");
  canvas.style.cssText =
    "position:fixed;inset:0;z-index:9999;pointer-events:none;";
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
  document.body.appendChild(canvas);

  const ctx = canvas.getContext("2d");
  if (!ctx) {
    canvas.remove();
    return;
  }

  const count = Math.min(
    Math.max(Math.round(particleCount), 20),
    200
  );
  const particles: Particle[] = [];
  for (let i = 0; i < count; i++) {
    particles.push(createParticle(canvas.width));
  }

  const start = performance.now();
  let animId: number;

  function draw(now: number) {
    const elapsed = now - start;

    if (elapsed >= DURATION) {
      cancelAnimationFrame(animId);
      canvas.remove();
      return;
    }

    ctx!.clearRect(0, 0, canvas.width, canvas.height);

    // Calculate fade factor for the tail end
    const fadeFactor =
      elapsed > FADE_START
        ? 1 - (elapsed - FADE_START) / (DURATION - FADE_START)
        : 1;

    for (const p of particles) {
      p.vy += GRAVITY;
      p.vx *= AIR_RESISTANCE;
      p.vy *= AIR_RESISTANCE;
      p.x += p.vx;
      p.y += p.vy;
      p.rotation += p.rotationSpeed;
      p.opacity = fadeFactor;

      ctx!.save();
      ctx!.translate(p.x, p.y);
      ctx!.rotate(p.rotation);
      ctx!.globalAlpha = p.opacity;
      ctx!.fillStyle = p.color;
      ctx!.fillRect(-p.width / 2, -p.height / 2, p.width, p.height);
      ctx!.restore();
    }

    animId = requestAnimationFrame(draw);
  }

  animId = requestAnimationFrame(draw);
}

/**
 * Play a short celebration "ding" sound via Web Audio API.
 * Only plays if sandcastle-sound-enabled is "true" in localStorage.
 */
export function playCelebrationSound(): void {
  try {
    if (localStorage.getItem("sandcastle-sound-enabled") !== "true") return;

    const audioCtx = new AudioContext();
    const oscillator = audioCtx.createOscillator();
    const gain = audioCtx.createGain();

    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(900, audioCtx.currentTime);
    gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.1);

    oscillator.connect(gain);
    gain.connect(audioCtx.destination);

    oscillator.start(audioCtx.currentTime);
    oscillator.stop(audioCtx.currentTime + 0.1);

    // Cleanup
    oscillator.onended = () => {
      oscillator.disconnect();
      gain.disconnect();
      void audioCtx.close();
    };
  } catch {
    // Silently ignore - audio is optional
  }
}
