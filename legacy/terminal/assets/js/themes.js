/**
 * AirX Portal - Theme System
 * Supports light/dark mode + color accent themes.
 * Persists user preference to localStorage.
 */

const THEMES = {
  dark: {
    '--bg-main':           '#0b121e',
    '--bg-elevated':       '#111c30',
    '--bg-card':           'rgba(18, 28, 45, 0.75)',
    '--bg-card-hover':     'rgba(26, 40, 64, 0.85)',
    '--bg-input':          'rgba(255, 255, 255, 0.04)',
    '--border-subtle':     'rgba(255, 255, 255, 0.08)',
    '--border-glow':       'rgba(159, 232, 112, 0.25)',
    '--border-color':      'rgba(255, 255, 255, 0.08)',
    '--text-main':         '#f8fafc',
    '--text-muted':        '#64748b',
    '--text-sub':          '#94a3b8',
    '--nav-bg':            'rgba(17, 28, 48, 0.85)',
    '--nav-border':        'rgba(255, 255, 255, 0.06)',
    '--nav-hover':         'rgba(255, 255, 255, 0.04)',
    '--shadow-sm':         '0 4px 12px -2px rgba(0, 0, 0, 0.4)',
    '--shadow-md':         '0 12px 32px -8px rgba(0, 0, 0, 0.5)',
    '--shadow-lg':         '0 24px 60px -12px rgba(0, 0, 0, 0.6)',
    '--body-bg':           'linear-gradient(135deg, #0b121e 0%, #111c30 100%)',
    '--body-glow-1':       'rgba(159, 232, 112, 0.04)',
    '--body-glow-2':       'rgba(56, 189, 248, 0.04)',
  },
  light: {
    '--bg-main':           '#f5f7fa',
    '--bg-elevated':       '#ffffff',
    '--bg-card':           'rgba(255, 255, 255, 0.85)',
    '--bg-card-hover':     'rgba(241, 245, 249, 0.95)',
    '--bg-input':          'rgba(0, 0, 0, 0.04)',
    '--border-subtle':     'rgba(0, 0, 0, 0.08)',
    '--border-glow':       'rgba(22, 163, 74, 0.25)',
    '--border-color':      'rgba(0, 0, 0, 0.1)',
    '--text-main':         '#1e293b',
    '--text-muted':        '#64748b',
    '--text-sub':          '#475569',
    '--nav-bg':            'rgba(255, 255, 255, 0.92)',
    '--nav-border':        'rgba(0, 0, 0, 0.08)',
    '--nav-hover':         'rgba(0, 0, 0, 0.05)',
    '--shadow-sm':         '0 4px 12px -2px rgba(0, 0, 0, 0.08)',
    '--shadow-md':         '0 12px 32px -8px rgba(0, 0, 0, 0.12)',
    '--shadow-lg':         '0 24px 60px -12px rgba(0, 0, 0, 0.16)',
    '--body-bg':           'linear-gradient(135deg, #f5f7fa 0%, #e8ecf1 100%)',
    '--body-glow-1':       'rgba(22, 163, 74, 0.05)',
    '--body-glow-2':       'rgba(56, 189, 248, 0.05)',
  }
};

const ACCENTS = {
  lime:     { '--wise-lime': '#9fe870', '--wise-lime-bg': 'rgba(159, 232, 112, 0.12)', '--primary': '#38bdf8', '--primary-bg': 'rgba(56, 189, 248, 0.12)', '--primary-glow': 'rgba(56, 189, 248, 0.2)', '--accent-green': '#10b981', '--accent-green-bg': 'rgba(16, 185, 129, 0.12)', '--accent-red': '#f43f5e', '--accent-red-bg': 'rgba(244, 63, 94, 0.12)', '--accent-yellow': '#f59e0b', '--accent-yellow-bg': 'rgba(245, 158, 11, 0.12)', '--accent-gold': '#d29922', '--accent-gold-bg': 'rgba(210, 153, 34, 0.12)' },
  blue:     { '--wise-lime': '#60a5fa', '--wise-lime-bg': 'rgba(96, 165, 250, 0.12)', '--primary': '#818cf8', '--primary-bg': 'rgba(129, 140, 248, 0.12)', '--primary-glow': 'rgba(129, 140, 248, 0.2)', '--accent-green': '#34d399', '--accent-green-bg': 'rgba(52, 211, 153, 0.12)', '--accent-red': '#fb7185', '--accent-red-bg': 'rgba(251, 113, 133, 0.12)', '--accent-yellow': '#fbbf24', '--accent-yellow-bg': 'rgba(251, 191, 36, 0.12)', '--accent-gold': '#f59e0b', '--accent-gold-bg': 'rgba(245, 158, 11, 0.12)' },
  purple:   { '--wise-lime': '#c084fc', '--wise-lime-bg': 'rgba(192, 132, 252, 0.12)', '--primary': '#e879f9', '--primary-bg': 'rgba(232, 121, 249, 0.12)', '--primary-glow': 'rgba(232, 121, 249, 0.2)', '--accent-green': '#a78bfa', '--accent-green-bg': 'rgba(167, 139, 250, 0.12)', '--accent-red': '#fb7185', '--accent-red-bg': 'rgba(251, 113, 133, 0.12)', '--accent-yellow': '#fbbf24', '--accent-yellow-bg': 'rgba(251, 191, 36, 0.12)', '--accent-gold': '#d946ef', '--accent-gold-bg': 'rgba(217, 70, 239, 0.12)' },
  gold:     { '--wise-lime': '#fbbf24', '--wise-lime-bg': 'rgba(251, 191, 36, 0.12)', '--primary': '#f59e0b', '--primary-bg': 'rgba(245, 158, 11, 0.12)', '--primary-glow': 'rgba(245, 158, 11, 0.2)', '--accent-green': '#d97706', '--accent-green-bg': 'rgba(217, 119, 6, 0.12)', '--accent-red': '#ef4444', '--accent-red-bg': 'rgba(239, 68, 68, 0.12)', '--accent-yellow': '#eab308', '--accent-yellow-bg': 'rgba(234, 179, 8, 0.12)', '--accent-gold': '#ca8a04', '--accent-gold-bg': 'rgba(202, 138, 4, 0.12)' },
  rose:     { '--wise-lime': '#fb7185', '--wise-lime-bg': 'rgba(251, 113, 133, 0.12)', '--primary': '#f472b6', '--primary-bg': 'rgba(244, 114, 182, 0.12)', '--primary-glow': 'rgba(244, 114, 182, 0.2)', '--accent-green': '#34d399', '--accent-green-bg': 'rgba(52, 211, 153, 0.12)', '--accent-red': '#e11d48', '--accent-red-bg': 'rgba(225, 29, 72, 0.12)', '--accent-yellow': '#f59e0b', '--accent-yellow-bg': 'rgba(245, 158, 11, 0.12)', '--accent-gold': '#be123c', '--accent-gold-bg': 'rgba(190, 18, 60, 0.12)' },
  emerald:  { '--wise-lime': '#34d399', '--wise-lime-bg': 'rgba(52, 211, 153, 0.12)', '--primary': '#10b981', '--primary-bg': 'rgba(16, 185, 129, 0.12)', '--primary-glow': 'rgba(16, 185, 129, 0.2)', '--accent-green': '#059669', '--accent-green-bg': 'rgba(5, 150, 105, 0.12)', '--accent-red': '#ef4444', '--accent-red-bg': 'rgba(239, 68, 68, 0.12)', '--accent-yellow': '#eab308', '--accent-yellow-bg': 'rgba(234, 179, 8, 0.12)', '--accent-gold': '#d97706', '--accent-gold-bg': 'rgba(217, 119, 6, 0.12)' }
};

function getSavedTheme() {
  try { return JSON.parse(localStorage.getItem('airx_theme')) || { mode: 'dark', accent: 'lime' }; }
  catch { return { mode: 'dark', accent: 'lime' }; }
}

function saveTheme(prefs) {
  localStorage.setItem('airx_theme', JSON.stringify(prefs));
}

function applyTheme(mode, accent) {
  const root = document.documentElement;
  const modeVars = THEMES[mode] || THEMES.dark;
  const accentVars = ACCENTS[accent] || ACCENTS.lime;

  Object.entries(modeVars).forEach(([k, v]) => root.style.setProperty(k, v));
  Object.entries(accentVars).forEach(([k, v]) => root.style.setProperty(k, v));

  // Update body background image
  const glow1 = modeVars['--body-glow-1'] || 'rgba(159,232,112,0.04)';
  const glow2 = modeVars['--body-glow-2'] || 'rgba(56,189,248,0.04)';
  document.body.style.backgroundImage = `radial-gradient(circle at 5% 5%, ${glow1} 0%, transparent 40%), radial-gradient(circle at 95% 95%, ${glow2} 0%, transparent 40%)`;
  document.body.style.backgroundAttachment = 'fixed';

  // For light mode, ensure text and inputs have better contrast
  if (mode === 'light') {
    root.style.setProperty('--nav-bg', 'rgba(255,255,255,0.92)');
    root.style.setProperty('--nav-border', 'rgba(0,0,0,0.08)');
    root.style.setProperty('--nav-hover', 'rgba(0,0,0,0.05)');
    root.style.setProperty('--input-border', 'rgba(0,0,0,0.12)');
  } else {
    root.style.setProperty('--nav-bg', 'rgba(17,28,48,0.85)');
    root.style.setProperty('--nav-border', 'rgba(255,255,255,0.06)');
    root.style.setProperty('--nav-hover', 'rgba(255,255,255,0.04)');
    root.style.setProperty('--input-border', 'rgba(255,255,255,0.08)');
  }

  // Update data attributes for CSS targeting
  document.documentElement.setAttribute('data-theme', mode);
  document.documentElement.setAttribute('data-accent', accent);
}

function toggleMode() {
  const saved = getSavedTheme();
  const newMode = saved.mode === 'dark' ? 'light' : 'dark';
  saveTheme({ ...saved, mode: newMode });
  applyTheme(newMode, saved.accent);
  return newMode;
}

function setAccent(accent) {
  const saved = getSavedTheme();
  saveTheme({ ...saved, accent });
  applyTheme(saved.mode, accent);
  return accent;
}

function initTheme() {
  const saved = getSavedTheme();
  applyTheme(saved.mode, saved.accent);
  return saved;
}

export { THEMES, ACCENTS, initTheme, toggleMode, setAccent, getSavedTheme, saveTheme, applyTheme };