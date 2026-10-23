import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, Text, View,
} from 'react-native';
import Slider from '@react-native-community/slider';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { useLight } from './src/useLight';
import { PatternPanel } from './src/PatternPanel';

const color = {
  background: '#101713', surface: '#1b251e', raised: '#253229', line: '#334237',
  text: '#f0f0dc', muted: '#a5b0a1', accent: '#d4ed92', ink: '#1a2614', error: '#ffbd9f',
};

function Button({ label, onPress, disabled = false, secondary = false }: {
  label: string; onPress: () => void; disabled?: boolean; secondary?: boolean;
}) {
  return (
    <Pressable accessibilityRole="button" accessibilityState={{ disabled }} onPress={onPress}
      disabled={disabled} style={({ pressed }) => [s.button, secondary && s.secondaryButton,
        disabled && s.disabled, pressed && s.pressed]}>
      <Text style={[s.buttonLabel, secondary && s.secondaryLabel]}>{label}</Text>
    </Pressable>
  );
}

function ValueControl({ title, value, min, max, unit, disabled, onCommit, marks }: {
  title: string; value: number; min: number; max: number; unit: string; disabled: boolean;
  onCommit: (value: number) => Promise<void>; marks: number[];
}) {
  const [draft, setDraft] = useState(value);
  const [dragging, setDragging] = useState(false);
  const actual = useRef(value);
  actual.current = value;
  useEffect(() => { if (!dragging) setDraft(value); }, [value, dragging]);
  const commit = async (target: number) => {
    setDragging(false);
    try { await onCommit(Math.round(target)); }
    finally { setDraft(actual.current); }
  };
  return (
    <View style={s.control}>
      <View style={s.rowBetween}>
        <Text style={s.controlTitle}>{title}</Text>
        <Text style={s.controlValue}>{Math.round(draft)}<Text style={s.unit}> {unit}</Text></Text>
      </View>
      <Slider accessibilityLabel={title} accessibilityValue={{ min, max, now: draft, text: `${draft} ${unit}` }}
        minimumValue={min} maximumValue={max} step={unit === 'K' ? 100 : 1} value={draft} disabled={disabled}
        onSlidingStart={() => setDragging(true)} onValueChange={setDraft}
        onSlidingComplete={(target) => { void commit(target); }}
        minimumTrackTintColor={color.accent} maximumTrackTintColor={color.line}
        thumbTintColor={disabled ? color.muted : color.accent} style={s.slider} />
      <View style={s.rowBetween}>
        <Text style={s.small}>{min} {unit}</Text>
        <Text style={s.small}>{max} {unit}</Text>
      </View>
      <View style={s.presets}>
        {marks.map((target) => (
          <Pressable key={target} accessibilityRole="button" accessibilityLabel={`Set ${title.toLowerCase()} to ${target} ${unit}`}
            disabled={disabled} onPress={() => { setDraft(target); void commit(target); }}
            style={({ pressed }) => [s.preset, disabled && s.disabled, pressed && s.pressed]}>
            <Text style={s.presetLabel}>{target}{unit === '%' ? '%' : ' K'}</Text>
          </Pressable>
        ))}
      </View>
    </View>
  );
}

function LightScreen() {
  const light = useLight();
  const actual = light.state;
  const connected = actual?.connected === true;
  const ready = connected && light.verified && !light.working;
  const [showDetails, setShowDetails] = useState(false);

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={s.page} keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag">
        <View style={s.header}>
          <View style={s.wordmark}><View style={s.brandMark} /><Text style={s.brand}>lighttime</Text></View>
          <Text style={s.headerTag}>LIGHT, ON YOUR TERMS</Text>
        </View>

        <Text style={s.eyebrow}>ZHIYUN · MOLUS G60</Text>
        <Text style={s.title}>Set the scene.</Text>
        <Text style={s.subtitle}>Your light. A little closer.</Text>

        <View style={s.connection}>
          <View style={s.rowBetween}>
            <View style={s.statusLine}>
              <View style={[s.dot, connected && light.verified ? s.dotLive : s.dotOffline]} />
              <Text style={s.status}>{light.status}</Text>
            </View>
            {(light.working || light.scanning) && <ActivityIndicator color={color.accent} size="small" />}
          </View>
          <Text accessibilityLiveRegion="polite" style={[s.connectionText, light.error && s.error]}>{light.message}</Text>
          {connected && <Text selectable style={s.deviceId}>{light.selected?.name ?? 'PL103'} · {actual.address}</Text>}

          <View style={s.actions}>
            {connected ? (
              <>
                <Button label="Read light" onPress={() => { void light.refresh(); }} disabled={light.working} />
                <Button label="Disconnect" secondary onPress={() => { void light.disconnect(); }} />
              </>
            ) : light.connecting ? (
              <Button label="Cancel connection" secondary onPress={() => { void light.disconnect(); }} />
            ) : light.scanning ? (
              <Button label="Stop search" secondary onPress={() => { void light.stopScan(); }} />
            ) : (
              <Button label={light.hasScanned ? 'Search again' : 'Find my light'}
                onPress={() => { void light.scan(); }} disabled={light.working || light.unsupported} />
            )}
            {light.permissionBlocked && <Button label="Open app settings" secondary onPress={() => { void Linking.openSettings(); }} />}
          </View>
        </View>

        {!connected && light.devices.length > 0 && (
          <View style={s.deviceList}>
            <Text style={s.sectionLabel}>NEARBY LIGHTS</Text>
            {light.devices.map((device) => (
              <Pressable key={device.id} accessibilityRole="button" accessibilityLabel={`Connect to ${device.name}`}
                disabled={light.working} onPress={() => { void light.connect(device); }}
                style={({ pressed }) => [s.deviceRow, light.working && s.disabled, pressed && s.pressed]}>
                <View style={s.deviceText}>
                  <Text style={s.deviceName}>{device.name}</Text>
                  <Text selectable style={s.small}>{device.id}</Text>
                </View>
                <View style={s.alignEnd}>
                  <Text style={s.connectLabel}>Connect ↗</Text>
                  {device.rssi !== null && <Text style={s.small}>{device.rssi} dBm</Text>}
                </View>
              </Pressable>
            ))}
          </View>
        )}

        {connected && actual.brightness !== null && actual.cct !== null ? (
          <>
            <View style={s.readback}>
              <View style={s.rowBetween}>
                <Text style={s.sectionLabel}>{light.verified ? 'READ FROM YOUR LIGHT' : 'LAST READ · UNVERIFIED'}</Text>
                <Text style={s.small}>{actual.updatedAt ? new Date(actual.updatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—'}</Text>
              </View>
              <View style={s.readbackValues}>
                <View><Text style={s.largeValue}>{Number(actual.brightness.toFixed(1))}<Text style={s.largeUnit}>%</Text></Text><Text style={s.small}>Brightness</Text></View>
                <View style={s.readbackDivider} />
                <View><Text style={s.largeValue}>{actual.cct}<Text style={s.largeUnit}> K</Text></Text><Text style={s.small}>Color temperature</Text></View>
              </View>
            </View>
            <View style={s.controlPanel}>
              <ValueControl title="Brightness" value={actual.brightness} min={0} max={100} unit="%"
                disabled={!ready} onCommit={(brightness) => light.update({ brightness })} marks={[5, 25, 50, 100]} />
              <View style={s.divider} />
              <ValueControl title="Color temperature" value={actual.cct} min={2700} max={6500} unit="K"
                disabled={!ready} onCommit={(cct) => light.update({ cct })} marks={[2700, 3200, 5600, 6500]} />
            </View>
            <View style={s.powerCard}>
              <View style={s.powerText}>
                <Text style={s.controlTitle}>{actual.power ? 'The light is on' : 'The light is dimmed off'}</Text>
                <Text style={s.small}>Off sets brightness to 0%. On restores the last level.</Text>
              </View>
              <Button label={actual.power ? 'Turn off' : 'Turn on'} secondary disabled={!ready}
                onPress={() => { void light.update({ power: !actual.power }); }} />
            </View>
            <PatternPanel status={light.effect} ready={ready} working={light.working} connected={connected}
              onStart={light.startEffect} onStop={light.stopEffect} />
          </>
        ) : (
          <View style={s.empty}>
            <View style={s.lightDrawing}>
              <View style={s.lampOuter}><View style={s.lampInner}><View style={s.lampCenter} /></View></View>
              <View style={s.lampStem} /><View style={s.lampFoot} />
            </View>
            <Text style={s.emptyTitle}>A good scene starts with light.</Text>
            <Text style={s.emptyText}>Switch on your G60, keep it nearby, then find your light to start shaping it.</Text>
          </View>
        )}

        <Pressable accessibilityRole="button" accessibilityState={{ expanded: showDetails }}
          onPress={() => setShowDetails(!showDetails)} style={s.detailsToggle}>
          <Text style={s.detailsLabel}>{showDetails ? '−' : '+'}  Connection details</Text>
        </Pressable>
        {showDetails && (
          <View style={s.details}>
            <Text style={s.detailsText}>Direct Bluetooth · Android development build</Text>
            <Text style={s.detailsText}>Search looks for PL103 lights. Internet and an account are not required.</Text>
            {actual?.device && <Text selectable style={s.detailsText}>Model: {actual.device.model}{'\n'}Serial: {actual.device.serial}{'\n'}Device ID: {actual.device.id}</Text>}
            <Text style={s.detailsText}>Brightness adjusts in whole percentages; color temperature adjusts in 100 K steps. Only completed readback is shown as verified. Disconnecting leaves the light at its current level. Backgrounding the app closes the connection.</Text>
            <Text style={s.detailsText}>Power is brightness-based; it does not switch mains power. If connected at 0%, the initial restore level is 5%.</Text>
          </View>
        )}
        <Text style={s.footer}>Made for the moment.</Text>
      </ScrollView>
    </SafeAreaView>
  );
}

export default function App() {
  return <SafeAreaProvider><LightScreen /></SafeAreaProvider>;
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: color.background },
  page: { padding: 24, paddingTop: 22, paddingBottom: 30, maxWidth: 640, width: '100%', alignSelf: 'center' },
  header: { marginBottom: 48, gap: 8 }, wordmark: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  brandMark: { width: 15, height: 15, borderRadius: 8, backgroundColor: color.accent },
  brand: { fontSize: 26, fontWeight: '600', letterSpacing: -1.2, color: color.text },
  headerTag: { fontSize: 9, letterSpacing: 2, color: color.muted, marginLeft: 24 },
  eyebrow: { fontSize: 10, letterSpacing: 2.5, color: color.accent, marginBottom: 14, fontWeight: '600' },
  title: { fontSize: 43, lineHeight: 49, letterSpacing: -2, color: color.text, fontWeight: '500' },
  subtitle: { fontSize: 16, color: color.muted, marginTop: 10, marginBottom: 28 },
  connection: { padding: 20, borderWidth: 1, borderColor: color.line, borderRadius: 18, gap: 13 },
  rowBetween: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 10 },
  statusLine: { flexDirection: 'row', alignItems: 'center', gap: 9, flexShrink: 1 },
  dot: { width: 7, height: 7, borderRadius: 4 }, dotLive: { backgroundColor: color.accent }, dotOffline: { backgroundColor: color.muted },
  status: { color: color.text, fontWeight: '600', fontSize: 14, flexShrink: 1 },
  connectionText: { color: color.muted, fontSize: 13, lineHeight: 20 },
  deviceId: { color: color.muted, fontSize: 11, fontFamily: 'monospace' },
  error: { color: color.error }, actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 3 },
  button: { backgroundColor: color.accent, paddingHorizontal: 17, paddingVertical: 14, minHeight: 48, borderRadius: 10, justifyContent: 'center' },
  buttonLabel: { fontSize: 13, fontWeight: '700', color: color.ink, textAlign: 'center' },
  secondaryButton: { backgroundColor: color.raised }, secondaryLabel: { color: color.text },
  disabled: { opacity: 0.4 }, pressed: { opacity: 0.65 },
  deviceList: { marginTop: 25 }, sectionLabel: { fontSize: 9, letterSpacing: 1.6, color: color.muted, fontWeight: '600', flexShrink: 1 },
  deviceRow: { borderBottomWidth: 1, borderBottomColor: color.line, paddingVertical: 20, flexDirection: 'row', justifyContent: 'space-between', gap: 14, minHeight: 78 },
  deviceText: { flex: 1, gap: 6 }, deviceName: { color: color.text, fontSize: 16, fontWeight: '500' },
  alignEnd: { alignItems: 'flex-end', gap: 6 }, connectLabel: { color: color.accent, fontSize: 13, fontWeight: '600' },
  small: { fontSize: 11, color: color.muted, lineHeight: 17 },
  readback: { marginTop: 32, marginBottom: 26 }, readbackValues: { flexDirection: 'row', alignItems: 'center', marginTop: 18, gap: 22, flexWrap: 'wrap' },
  largeValue: { fontSize: 39, color: color.text, letterSpacing: -1.5, fontVariant: ['tabular-nums'] },
  largeUnit: { fontSize: 17, color: color.muted, letterSpacing: 0 },
  readbackDivider: { width: 1, height: 42, backgroundColor: color.line },
  controlPanel: { backgroundColor: color.surface, borderRadius: 18, paddingHorizontal: 20 }, control: { paddingVertical: 24 },
  controlTitle: { fontSize: 15, color: color.text, fontWeight: '500', flexShrink: 1 },
  controlValue: { fontSize: 23, letterSpacing: -0.5, color: color.text, fontVariant: ['tabular-nums'] }, unit: { fontSize: 12, color: color.muted },
  slider: { width: '100%', height: 50, marginTop: 15 }, presets: { flexDirection: 'row', flexWrap: 'wrap', gap: 7, marginTop: 19 },
  preset: { backgroundColor: color.raised, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 14, minHeight: 48, justifyContent: 'center' },
  presetLabel: { color: color.text, fontSize: 11 }, divider: { height: 1, backgroundColor: color.line },
  powerCard: { gap: 18, marginTop: 24, borderWidth: 1, borderColor: color.line, borderRadius: 18, padding: 20 }, powerText: { gap: 8 },
  empty: { paddingVertical: 40, alignItems: 'center' }, lightDrawing: { alignItems: 'center', marginBottom: 28, opacity: 0.85 },
  lampOuter: { width: 96, height: 96, borderRadius: 48, borderColor: color.line, borderWidth: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: color.surface },
  lampInner: { width: 70, height: 70, borderRadius: 35, borderColor: color.muted, borderWidth: 1, alignItems: 'center', justifyContent: 'center' },
  lampCenter: { width: 39, height: 39, borderRadius: 20, backgroundColor: color.accent },
  lampStem: { height: 25, width: 1, backgroundColor: color.line }, lampFoot: { height: 1, width: 54, backgroundColor: color.line },
  emptyTitle: { color: color.text, fontSize: 16, marginBottom: 12, textAlign: 'center' }, emptyText: { color: color.muted, fontSize: 13, lineHeight: 21, maxWidth: 260, textAlign: 'center' },
  detailsToggle: { borderTopWidth: 1, borderTopColor: color.line, paddingVertical: 19, minHeight: 48 }, detailsLabel: { color: color.muted, fontSize: 12 },
  details: { gap: 13, paddingBottom: 22 }, detailsText: { color: color.muted, fontSize: 12, lineHeight: 19 },
  footer: { textAlign: 'center', fontSize: 10, letterSpacing: 1, color: color.muted, marginTop: 24 },
});
