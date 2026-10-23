import { useMemo, useState } from 'react';
import { Pressable, StyleSheet, Switch, Text, View } from 'react-native';
import type { EffectConfig, EffectStatus } from '@lighttime/sdk';
import { createEffectForm, EFFECT_CHOICES, effectConfigFromForm, supportsUntilStopped } from './effectForm';
import type { EffectForm } from './effectForm';
import { KeyframeEditor } from './pattern-ui/KeyframeEditor';
import { PatternBrowser } from './pattern-ui/PatternBrowser';
import { ScenePreview } from './pattern-ui/ScenePreview';
import { Chip, Field, Range } from './pattern-ui/TuningControls';
import { colors as c } from './pattern-ui/palette';
import { previewEffect } from './pattern-ui/preview';
import { draftsFromFrames, keyframesFromDrafts } from './pattern-ui/keyframes';
import type { FrameDraft } from './pattern-ui/keyframes';

export function PatternPanel({ status, ready, working, connected, onStart, onStop }: {
  status: EffectStatus; ready: boolean; working: boolean; connected: boolean;
  onStart: (config: EffectConfig) => Promise<void>; onStop: () => Promise<void>;
}) {
  const [form, setForm] = useState(() => createEffectForm('candle'));
  const [frameDrafts, setFrameDrafts] = useState<FrameDraft[]>([]);
  const [expanded, setExpanded] = useState(false);
  const change = <K extends keyof EffectForm>(key: K, value: EffectForm[K]) => setForm(previous => ({ ...previous, [key]: value }));
  const choice = EFFECT_CHOICES.find(item => item.id === form.id)!;
  const pattern = form.base.pattern;
  const finiteOnly = !supportsUntilStopped(form);
  const keyframes = form.base.pattern === 'keyframes' ? form.base.keyframes : null;
  const hasPeriod = ['blink', 'breathe', 'cct-sweep'].includes(pattern);
  const hasEnd = ['flicker', 'sunrise', 'emergency', 'tv', 'cct-sweep'].includes(pattern);
  const hasSeed = ['flicker', 'lightning', 'paparazzi', 'tv'].includes(pattern);
  const hasCurve = ['breathe', 'fade-in', 'fade-out', 'sunrise'].includes(pattern);
  const hasBaselineCct = useMemo(() => createEffectForm(form.id).cct === '', [form.id]);
  const requested = useMemo(() => {
    try {
      const config = effectConfigFromForm(form);
      const result = previewEffect(config);
      return { config: result.preview ? config : null, ...result };
    } catch (error) {
      return { config: null, preview: null, error: error instanceof Error ? error.message : 'Check the requested settings.' };
    }
  }, [form]);
  const progress = status.progress === null ? null : Math.round(status.progress * 100);
  const startDisabled = !ready || !requested.config;
  const stopDisabled = !connected || working || !status.running;
  const note = pattern === 'keyframes' ? 'Your sequence of brightness and white temperature.'
    : pattern === 'sunrise' && form.base.pattern === 'sunrise' && form.base.direction !== 'set'
      ? 'A gradual journey from warm white to daylight.' : choice.note;

  return <View style={s.panel}>
    <View style={s.intro}>
      <View style={s.between}><Text accessibilityRole="header" style={s.title}>Patterns</Text><Text style={s.hint}>16 scenes</Text></View>
      <Text style={s.description}>Preview a scene, then make it yours. The light changes when you press Start.</Text>
    </View>
    <PatternBrowser selected={form.id} preview={requested.preview} onSelect={id => {
      if (id !== form.id) {
        const next = createEffectForm(id);
        setForm(next);
        setFrameDrafts(next.base.pattern === 'keyframes' ? draftsFromFrames(next.base.keyframes) : []);
      }
    }} />
    <ScenePreview title={choice.title} note={note} preview={requested.preview} />

    <Pressable accessibilityRole="button" accessibilityState={{ expanded }} onPress={() => setExpanded(!expanded)}
      style={({ pressed }) => [s.tuneToggle, pressed && s.pressed]}>
      <View style={s.tuneText}><Text style={s.tuneLabel}>Tune pattern</Text><Text style={s.hint}>{keyframes ? 'Frames, easing & run time' : 'Brightness, warmth & run time'}</Text></View>
      <Text accessible={false} style={s.toggleIcon}>{expanded ? '−' : '+'}</Text>
    </Pressable>
    {expanded && <View style={s.tuning}>
      {keyframes ? <KeyframeEditor drafts={frameDrafts} onChange={drafts => {
        setFrameDrafts(drafts);
        setForm(previous => previous.base.pattern === 'keyframes'
          ? { ...previous, base: { ...previous.base, keyframes: keyframesFromDrafts(drafts) } } : previous);
      }} /> : <>
        <Range label="Minimum brightness" value={form.minBrightness} min={0} max={100} step={1} unit="%" onChange={value => change('minBrightness', value)} />
        <Range label="Maximum brightness" value={form.maxBrightness} min={0} max={100} step={1} unit="%" onChange={value => change('maxBrightness', value)} />
        <Range label={hasEnd ? 'Start temperature' : 'Color temperature'} value={form.cct} min={2700} max={6500} step={100} unit="K" onChange={value => change('cct', value)} />
        <Text style={s.hint}>{form.cct ? 'White temperature, in 100 K steps.' : 'Uses the verified temperature captured when Start is pressed.'}</Text>
        {hasBaselineCct && <Chip label="Use temperature at Start" selected={form.cct === ''} onPress={() => change('cct', '')} />}
        {hasEnd && <Range label="End temperature" value={form.cctEnd} min={2700} max={6500} step={100} unit="K" onChange={value => change('cctEnd', value)} />}
      </>}

      <View style={s.rule} />
      <Text style={s.fieldLabel}>Run time</Text>
      <View style={s.chips}>
        {!finiteOnly && <Chip label="Until stopped" selected={form.untilStopped} onPress={() => change('untilStopped', true)} />}
        {[10, 60, 900].map(seconds => <Chip key={seconds} label={seconds === 900 ? '15 min' : `${seconds} s`}
          selected={!form.untilStopped && form.duration === String(seconds)}
          onPress={() => setForm(previous => ({ ...previous, duration: String(seconds), untilStopped: false }))} />)}
      </View>
      {!form.untilStopped && <Field label="Duration · seconds" value={form.duration} onChange={value => change('duration', value)} hint="1–3600 seconds" />}
      {hasPeriod && <Field label="Period · seconds" value={form.period} onChange={value => change('period', value)}
        hint={pattern === 'blink' ? '0.2–60 s for one on/off cycle. Each phase must last at least 0.1 s.' : '1–60 s for one full cycle.'} />}
      {form.base.pattern === 'blink' && <Range label="On time" value={String(Math.round((form.base.duty ?? 0.5) * 100))}
        min={0} max={100} step={1} unit="%" onChange={value => setForm(previous => previous.base.pattern === 'blink'
          ? { ...previous, base: { ...previous.base, duty: Number(value) / 100 } } : previous)} />}
      {hasCurve && <View style={s.optionGroup}>
        <Text style={s.fieldLabel}>Brightness curve</Text>
        <View style={s.chips}>{(['linear', 'perceptual'] as const).map(curve => <Chip key={curve}
          label={curve === 'linear' ? 'Linear' : 'Perceptual'} selected={(form.base.curve ?? (pattern === 'sunrise' ? 'perceptual' : 'linear')) === curve}
          onPress={() => setForm(previous => ({ ...previous, base: { ...previous.base, curve } }))} />)}</View>
        <Text style={s.hint}>Linear changes evenly. Perceptual keeps more of the transition at softer levels.</Text>
      </View>}
      {pattern === 'pulse' && <Field label="Pulse · beats per minute" value={form.bpm} onChange={value => change('bpm', value)} hint="Heartbeat: 20–120 BPM" />}
      {pattern === 'morse' && <>
        <Field label="Morse message" text value={form.text} onChange={value => change('text', value)} hint="Up to 64 letters, digits, spaces or . , ? / = + - @" />
        <Field label="Morse dot · seconds" value={form.unit} onChange={value => change('unit', value)} hint="0.1–2 s per dot" />
      </>}
      {hasSeed && <Field label="Random seed" value={form.seed} onChange={value => change('seed', value)} hint="0–4294967295. The same seed repeats the same sequence." />}
      {!form.untilStopped && <View style={s.switchRow}>
        <View style={s.switchText}><Text style={s.fieldLabel}>Restore when finished</Text><Text style={s.hint}>Return to both settings captured at Start.</Text></View>
        <Switch accessibilityLabel="Restore baseline on completion" value={form.restoreOnComplete} onValueChange={value => change('restoreOnComplete', value)}
          hitSlop={8} trackColor={{ false: c.line, true: c.accent }} thumbColor={form.restoreOnComplete ? c.ink : c.muted} style={s.switch} />
      </View>}
    </View>}

    {requested.error && <View style={s.validation}>
      <Text style={s.fieldLabel}>Check tuning</Text>
      <Text accessibilityLiveRegion="polite" style={s.error}>{requested.error}</Text>
    </View>}
    <View style={s.runner}>
      <View style={s.between}>
        <Text accessibilityLiveRegion="polite" style={s.runnerTitle}>{status.running ? `Running · ${status.pattern}` : status.id ? `${progress === 100 ? 'Finished' : 'Stopped'} · ${status.pattern}` : 'No pattern running'}</Text>
        {progress !== null && <Text style={s.progressText}>{progress}%</Text>}
      </View>
      {progress !== null && <View accessibilityRole="progressbar" accessibilityLabel="Pattern progress" accessibilityValue={{ min: 0, max: 100, now: progress }} style={s.progressTrack}>
        <View style={[s.progressFill, { width: `${progress}%` }]} />
      </View>}
      {status.running && status.endsAt === null && <Text style={s.hint}>Until stopped</Text>}
      {status.lastError && <Text selectable accessibilityLiveRegion="polite" style={s.error}>{status.lastError}</Text>}
      <Text style={s.hint}>Actual output appears in the light’s readback. Timing follows Bluetooth and readback speed.</Text>
    </View>
    <View style={s.actions}>
      <Pressable accessibilityRole="button" disabled={startDisabled} accessibilityState={{ disabled: startDisabled }}
        onPress={() => { if (requested.config) void onStart(requested.config); }}
        style={({ pressed }) => [s.start, startDisabled && s.disabled, pressed && s.pressed]}>
        <Text style={s.startText}>{status.running ? 'Start new pattern' : 'Start pattern'}</Text>
      </Pressable>
      {status.running && <Pressable accessibilityRole="button" disabled={stopDisabled} accessibilityState={{ disabled: stopDisabled }}
        onPress={() => { void onStop(); }} style={({ pressed }) => [s.stop, stopDisabled && s.disabled, pressed && s.pressed]}>
        <Text style={s.stopText}>Stop & restore</Text>
      </Pressable>}
    </View>
    <Text style={s.ownership}>Runs on this phone. Keep Lighttime open. Backgrounding or disconnecting stops the pattern and leaves the current settings. Manual brightness, temperature or power changes also stop it.</Text>
  </View>;
}

const s = StyleSheet.create({
  panel: { paddingTop: 8, paddingBottom: 20, gap: 20 }, intro: { gap: 8 },
  between: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  title: { color: c.text, fontSize: 24, letterSpacing: -0.5, fontWeight: '500', flexShrink: 1 },
  description: { color: c.muted, fontSize: 14, lineHeight: 22 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 }, hint: { color: c.muted, fontSize: 12, lineHeight: 19 },
  tuneToggle: { minHeight: 72, flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 16,
    borderRadius: 18, backgroundColor: c.raised, paddingVertical: 14, paddingHorizontal: 18 },
  tuneText: { flex: 1, gap: 3 }, tuneLabel: { color: c.text, fontSize: 16, fontWeight: '500' }, toggleIcon: { color: c.accent, fontSize: 26 },
  tuning: { backgroundColor: c.surface, borderRadius: 20, padding: 18, gap: 22 },
  fieldLabel: { color: c.text, fontSize: 14, lineHeight: 21, fontWeight: '500' },
  rule: { height: 1, backgroundColor: c.line }, optionGroup: { gap: 12 },
  switchRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 12 }, switchText: { flexGrow: 1, flexBasis: 180, gap: 6 },
  switch: { minHeight: 48, minWidth: 48 },
  runner: { paddingHorizontal: 2, paddingVertical: 4, gap: 10 }, runnerTitle: { color: c.text, fontSize: 14, fontWeight: '500', flexShrink: 1 },
  progressText: { color: c.accent, fontSize: 13, fontVariant: ['tabular-nums'] },
  progressTrack: { backgroundColor: c.line, borderRadius: 3, height: 4, overflow: 'hidden' }, progressFill: { height: 4, backgroundColor: c.accent },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  start: { flexGrow: 1, flexBasis: 150, backgroundColor: c.accent, padding: 16, minHeight: 54, borderRadius: 16, justifyContent: 'center' },
  startText: { color: c.ink, fontSize: 15, fontWeight: '600', textAlign: 'center' },
  stop: { flexGrow: 1, flexBasis: 130, backgroundColor: c.raised, padding: 16, minHeight: 54, borderRadius: 16, justifyContent: 'center' },
  stopText: { color: c.text, fontSize: 15, fontWeight: '500', textAlign: 'center' },
  ownership: { color: c.muted, fontSize: 12, lineHeight: 19 }, validation: { padding: 16, borderWidth: 1, borderColor: c.error, borderRadius: 14, gap: 6 },
  error: { color: c.error, fontSize: 13, lineHeight: 20 }, pressed: { opacity: 0.65 }, disabled: { opacity: 0.4 },
});
