import { useState } from 'react';
import { Pressable, StyleSheet, Switch, Text, TextInput, View } from 'react-native';
import Slider from '@react-native-community/slider';
import { parseEffectConfig } from '@lighttime/sdk';
import type { EffectConfig, EffectStatus } from '@lighttime/sdk';
import { createEffectForm, EFFECT_CHOICES, effectConfigFromForm, supportsUntilStopped } from './effectForm';
import type { EffectForm } from './effectForm';

const c = {
  surface: '#1b251e', raised: '#253229', line: '#334237', text: '#f0f0dc',
  muted: '#a5b0a1', accent: '#d4ed92', ink: '#1a2614', error: '#ffbd9f',
};

function Chip({ label, selected, onPress }: { label: string; selected?: boolean; onPress: () => void }) {
  return <Pressable accessibilityRole="button" accessibilityState={{ selected }} onPress={onPress}
    style={({ pressed }) => [s.chip, selected && s.chipSelected, pressed && s.pressed]}>
    <Text style={[s.chipText, selected && s.chipTextSelected]}>{label}</Text>
  </Pressable>;
}

function Field({ label, value, onChange, hint, text = false }: {
  label: string; value: string; onChange: (value: string) => void; hint?: string; text?: boolean;
}) {
  return <View style={s.field}>
    <Text style={s.fieldLabel}>{label}</Text>
    <TextInput accessibilityLabel={label} value={value} onChangeText={onChange}
      keyboardType={text ? 'default' : 'decimal-pad'} autoCorrect={false} autoCapitalize={text ? 'characters' : 'none'}
      maxLength={text ? 64 : 12} selectTextOnFocus style={s.input} selectionColor={c.accent} />
    {hint && <Text style={s.hint}>{hint}</Text>}
  </View>;
}

function Range({ label, value, min, max, step, unit, onChange }: {
  label: string; value: string; min: number; max: number; step: number; unit: string; onChange: (value: string) => void;
}) {
  const numeric = Number(value);
  const current = Math.max(min, Math.min(max, Number.isFinite(numeric) && value !== '' ? numeric : min));
  return <View style={s.range}>
    <View style={s.between}><Text style={s.fieldLabel}>{label}</Text><Text style={s.rangeValue}>{value || 'Baseline'}{value && ` ${unit}`}</Text></View>
    <Slider accessibilityLabel={`Requested ${label.toLowerCase()}`} minimumValue={min} maximumValue={max} step={step}
      accessibilityValue={{ min, max, now: current, text: value ? `${value} ${unit}` : 'Verified baseline' }}
      value={current} onValueChange={next => onChange(String(Math.round(next)))}
      minimumTrackTintColor={c.accent} maximumTrackTintColor={c.line} thumbTintColor={c.accent} style={s.slider} />
  </View>;
}

export function PatternPanel({ status, ready, working, connected, onStart, onStop }: {
  status: EffectStatus; ready: boolean; working: boolean; connected: boolean;
  onStart: (config: EffectConfig) => Promise<void>; onStop: () => Promise<void>;
}) {
  const [form, setForm] = useState(() => createEffectForm('candle'));
  const [expanded, setExpanded] = useState(false);
  const change = <K extends keyof EffectForm>(key: K, value: EffectForm[K]) => setForm(previous => ({ ...previous, [key]: value }));
  const choice = EFFECT_CHOICES.find(item => item.id === form.id)!;
  const pattern = form.base.pattern;
  const finiteOnly = !supportsUntilStopped(form);
  const keyframes = form.base.pattern === 'keyframes' ? form.base.keyframes : null;
  const hasPeriod = ['blink', 'breathe', 'cct-sweep'].includes(pattern);
  const hasEnd = ['flicker', 'sunrise', 'emergency', 'tv', 'cct-sweep'].includes(pattern);
  const hasSeed = ['flicker', 'lightning', 'paparazzi', 'tv'].includes(pattern);
  const hasBaselineCct = parseEffectConfig(form.base).cct === null;
  let config: EffectConfig | null = null;
  let validationError: string | null = null;
  try { config = effectConfigFromForm(form); }
  catch (error) { validationError = error instanceof Error ? error.message : 'Check the requested settings.'; }
  const progress = status.progress === null ? null : Math.round(status.progress * 100);

  return <View style={s.panel}>
    <View style={s.between}>
      <Text style={s.eyebrow}>PATTERNS</Text>
      <Text style={s.badge}>RUNS ON THIS PHONE</Text>
    </View>
    <Text style={s.title}>A little atmosphere.</Text>
    <Text style={s.description}>Choose a scene, make it yours, then press Start. Changing these settings leaves the light as it is.</Text>

    <ScrollChoices selected={form.id} onSelect={id => setForm(createEffectForm(id))} />
    <View style={s.selection}>
      <Text style={s.selectionTitle}>{choice.title}</Text>
      <Text style={s.description}>{choice.note}</Text>
      <Text style={s.hint}>REQUESTED · {keyframes ? `${keyframes.length} keyframes · ${keyframes[keyframes.length - 1]!.at} s loop` : `${form.minBrightness}–${form.maxBrightness}%`}{' · '}{form.untilStopped ? 'until stopped' : `${form.duration} s`}</Text>
    </View>

    <Pressable accessibilityRole="button" accessibilityState={{ expanded }} onPress={() => setExpanded(!expanded)} style={s.tuneToggle}>
      <Text style={s.tuneLabel}>{expanded ? '−  Close tuning' : '+  Tune this pattern'}</Text>
    </Pressable>
    {expanded && <View style={s.tuning}>
      {keyframes ? <View style={s.sequence}>
        <Text style={s.fieldLabel}>Scene sequence</Text>
        {keyframes.map((frame, i) => <Text key={frame.at} style={s.hint}>
          {frame.at} s  ·  {frame.brightness === undefined ? 'carry brightness' : `${frame.brightness}%`}  ·  {frame.cct === undefined ? 'carry CCT' : `${frame.cct} K`}{i > 0 ? `  ·  ${frame.ease ?? 'linear'}` : ''}
        </Text>)}
        <Text style={s.hint}>This native demonstration uses fixed frames. Custom frame editing is available in the browser controller.</Text>
      </View> : <>
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
        hint={pattern === 'blink' ? '0.2–60 s. Even on/off phases, each at least 0.1 s.' : '1–60 s for one full cycle.'} />}
      {pattern === 'pulse' && <Field label="Pulse · beats per minute" value={form.bpm} onChange={value => change('bpm', value)} hint="Heartbeat: 20–120 BPM" />}
      {pattern === 'morse' && <>
        <Field label="Morse message" text value={form.text} onChange={value => change('text', value)} hint="Up to 64 letters, digits, spaces or . , ? / = + - @" />
        <Field label="Morse dot · seconds" value={form.unit} onChange={value => change('unit', value)} hint="0.1–2 s per dot" />
      </>}
      {hasSeed && <Field label="Random seed" value={form.seed} onChange={value => change('seed', value)} hint="0–4294967295. The same seed repeats the same sequence." />}
      {!form.untilStopped && <View style={s.switchRow}>
        <View style={s.switchText}><Text style={s.fieldLabel}>Restore when finished</Text><Text style={s.hint}>Return to both settings captured at Start.</Text></View>
        <Switch accessibilityLabel="Restore baseline on completion" value={form.restoreOnComplete} onValueChange={value => change('restoreOnComplete', value)}
          trackColor={{ false: c.line, true: c.accent }} thumbColor={form.restoreOnComplete ? c.ink : c.muted} />
      </View>}
    </View>}

    {validationError && <Text accessibilityLiveRegion="polite" style={s.error}>{validationError}</Text>}
    <View style={s.runner}>
      <View style={s.between}>
        <Text accessibilityLiveRegion="polite" style={s.runnerTitle}>{status.running ? `Running · ${status.pattern}` : status.id ? `${progress === 100 ? 'Finished' : 'Stopped'} · ${status.pattern}` : 'No pattern running'}</Text>
        {progress !== null && <Text style={s.progressText}>{progress}%</Text>}
      </View>
      {progress !== null && <View accessibilityRole="progressbar" accessibilityValue={{ min: 0, max: 100, now: progress }} style={s.progressTrack}>
        <View style={[s.progressFill, { width: `${progress}%` }]} />
      </View>}
      {status.running && status.endsAt === null && <Text style={s.hint}>Until stopped</Text>}
      {status.lastError && <Text selectable accessibilityLiveRegion="polite" style={s.error}>{status.lastError}</Text>}
      <Text style={s.hint}>Actual output appears in the readback above. Timing follows Bluetooth and readback speed.</Text>
    </View>
    <View style={s.actions}>
      <Pressable accessibilityRole="button" disabled={!ready || !config} accessibilityState={{ disabled: !ready || !config }}
        onPress={() => { if (config) void onStart(config); }} style={({ pressed }) => [s.start, (!ready || !config) && s.disabled, pressed && s.pressed]}>
        <Text style={s.startText}>{status.running ? 'Start new pattern' : 'Start pattern'}</Text>
      </Pressable>
      <Pressable accessibilityRole="button" disabled={!connected || working || !status.running} accessibilityState={{ disabled: !connected || working || !status.running }}
        onPress={() => { void onStop(); }} style={({ pressed }) => [s.stop, (!connected || working || !status.running) && s.disabled, pressed && s.pressed]}>
        <Text style={s.stopText}>Stop & restore</Text>
      </Pressable>
    </View>
    <Text style={s.ownership}>Keep Lighttime open. Backgrounding or disconnecting stops the pattern and leaves the current settings. Manual brightness, temperature or power changes also stop it.</Text>
  </View>;
}

function ScrollChoices({ selected, onSelect }: { selected: EffectForm['id']; onSelect: (id: EffectForm['id']) => void }) {
  return <View style={s.chips}>{EFFECT_CHOICES.map(choice => <Chip key={choice.id} label={choice.title} selected={choice.id === selected} onPress={() => onSelect(choice.id)} />)}</View>;
}

const s = StyleSheet.create({
  panel: { marginTop: 32, marginBottom: 28, borderTopWidth: 1, borderColor: c.line, paddingTop: 28, gap: 18 },
  between: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12 },
  eyebrow: { color: c.accent, fontSize: 10, fontWeight: '600', letterSpacing: 2 },
  badge: { color: c.muted, fontSize: 8, letterSpacing: 1 },
  title: { color: c.text, fontSize: 29, letterSpacing: -0.8, fontWeight: '500' },
  description: { color: c.muted, fontSize: 13, lineHeight: 20 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 13, paddingVertical: 11, borderRadius: 9, backgroundColor: c.raised, borderWidth: 1, borderColor: c.line, alignSelf: 'flex-start' },
  chipSelected: { backgroundColor: c.accent, borderColor: c.accent }, chipText: { color: c.text, fontSize: 12 }, chipTextSelected: { color: c.ink, fontWeight: '600' },
  selection: { borderLeftWidth: 2, borderColor: c.accent, paddingLeft: 15, gap: 6 }, selectionTitle: { color: c.text, fontSize: 18, fontWeight: '500' },
  hint: { color: c.muted, fontSize: 11, lineHeight: 18 },
  tuneToggle: { minHeight: 48, justifyContent: 'center', borderBottomWidth: 1, borderColor: c.line }, tuneLabel: { color: c.accent, fontSize: 13 },
  tuning: { backgroundColor: c.surface, borderRadius: 16, padding: 18, gap: 18 },
  range: { gap: 4 }, rangeValue: { color: c.text, fontSize: 15, fontVariant: ['tabular-nums'] }, slider: { width: '100%', height: 44 },
  field: { gap: 8 }, fieldLabel: { color: c.text, fontSize: 12, fontWeight: '500' },
  input: { color: c.text, backgroundColor: c.raised, borderWidth: 1, borderColor: c.line, borderRadius: 9, paddingHorizontal: 13, paddingVertical: 12, minHeight: 48, fontSize: 16, fontVariant: ['tabular-nums'] },
  rule: { height: 1, backgroundColor: c.line }, sequence: { gap: 10 },
  switchRow: { flexDirection: 'row', alignItems: 'center', gap: 12 }, switchText: { flex: 1, gap: 6 },
  runner: { padding: 16, borderWidth: 1, borderColor: c.line, borderRadius: 12, gap: 10 }, runnerTitle: { color: c.text, fontSize: 13, fontWeight: '500', flexShrink: 1 },
  progressText: { color: c.accent, fontSize: 12, fontVariant: ['tabular-nums'] }, progressTrack: { backgroundColor: c.line, borderRadius: 3, height: 4, overflow: 'hidden' }, progressFill: { height: 4, backgroundColor: c.accent },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  start: { flexGrow: 1, backgroundColor: c.accent, padding: 15, minHeight: 48, borderRadius: 10, justifyContent: 'center' }, startText: { color: c.ink, fontSize: 13, fontWeight: '700', textAlign: 'center' },
  stop: { flexGrow: 1, backgroundColor: c.raised, padding: 15, minHeight: 48, borderRadius: 10, justifyContent: 'center' }, stopText: { color: c.text, fontSize: 13, fontWeight: '600', textAlign: 'center' },
  ownership: { color: c.muted, fontSize: 11, lineHeight: 18 }, error: { color: c.error, fontSize: 12, lineHeight: 19 },
  pressed: { opacity: 0.65 }, disabled: { opacity: 0.4 },
});
