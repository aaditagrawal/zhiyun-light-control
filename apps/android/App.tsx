import { useState } from 'react';
import {
  ActivityIndicator, Linking, Pressable, ScrollView, StyleSheet, Switch, Text,
  useWindowDimensions, View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { useLight } from './src/useLight';
import { PatternPanel } from './src/PatternPanel';
import { Button } from './src/ui/Button';
import { IndicativeSlider } from './src/ui/IndicativeSlider';
import { color, minimumTouchTarget, temperatureSwatch } from './src/ui/theme';

type Destination = 'controls' | 'patterns';

function deviceLabel(name: string | undefined): string {
  const suffix = name?.replace(/^PL103[\s_-]*/i, '').trim();
  return suffix ? `MOLUS G60 · ${suffix}` : 'MOLUS G60';
}

function signalLabel(rssi: number | null): string {
  if (rssi === null) return 'Signal unavailable';
  return rssi >= -60 ? 'Strong signal' : rssi >= -75 ? 'Good signal' : 'Weak signal · move closer';
}

function Signal({ rssi }: { rssi: number | null }) {
  const bars = rssi === null ? 0 : rssi >= -60 ? 3 : rssi >= -75 ? 2 : 1;
  return (
    <View accessible={false} style={s.signal}>
      {[0, 1, 2].map(i => <View key={i} style={[s.signalBar, { height: 6 + i * 5, backgroundColor: i < bars ? color.accent : color.line }]} />)}
    </View>
  );
}

function LightScreen() {
  const light = useLight();
  const actual = light.state;
  const connected = actual?.connected === true && !light.connecting;
  const ready = connected && light.verified && !light.working;
  const hasValues = connected && actual.brightness !== null && actual.cct !== null;
  const [showDetails, setShowDetails] = useState(false);
  const [destination, setDestination] = useState<Destination>('controls');
  const [actionError, setActionError] = useState<string | null>(null);
  const { width, fontScale } = useWindowDimensions();
  const stacked = width < 350 || fontScale > 1.25;
  const busy = light.working || light.scanning;
  const issue = actionError !== null || (!light.working && light.error) || (connected && !light.verified && !light.working);
  const readTime = actual?.updatedAt ? new Date(actual.updatedAt).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }) : null;

  // Teardown remains owned by useLight. Catch UI-triggered failures without leaving a rejected promise.
  const perform = (operation: () => Promise<unknown>) => {
    setActionError(null);
    void operation().catch(error => {
      setActionError(error instanceof Error ? error.message : 'That did not finish. Try again.');
    });
  };
  const openSettings = () => {
    setActionError(null);
    void Linking.openSettings().catch(() => {
      setActionError('Open Android Settings, then Apps → Lighttime → Permissions to allow Bluetooth access.');
    });
  };

  const heading = connected ? deviceLabel(light.selected?.name)
    : light.connecting ? 'Connecting to your light'
      : light.scanning ? 'Looking for your light'
        : light.unsupported ? 'Bluetooth unavailable'
          : light.permissionBlocked ? 'Allow Bluetooth access'
            : issue ? light.status : 'Find your light';
  const message = actionError ?? (light.unsupported
    ? 'Use Lighttime on an Android device with Bluetooth Low Energy. See connection details for more information.'
    : light.connecting ? 'Connecting and reading current settings. Keep your G60 nearby.'
      : light.scanning ? 'Keep your G60 switched on and nearby. Search takes up to 10 seconds.'
        : light.message.replace(/\bPL103\b/g, 'G60'));

  return (
    <SafeAreaView style={s.safe}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={s.page} keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag" showsVerticalScrollIndicator>
        <View style={s.header}>
          <Text accessibilityRole="header" style={s.brand}>Lighttime</Text>
          <Text style={s.headerNote}>Bluetooth light control</Text>
        </View>

        <View style={[s.connection, issue && s.connectionIssue]}>
          <View style={[s.connectionHeading, stacked && s.stacked]}>
            <View style={s.deviceHeading}>
              <View accessible={false} style={[s.lightFace, connected && light.verified && {
                borderColor: color.accent,
                backgroundColor: actual?.power && actual.cct !== null ? temperatureSwatch(actual.cct) : color.raised,
              }]}>
                <View style={[s.lightFaceCenter, connected && light.verified && actual?.power && s.lightFaceLit]} />
              </View>
              <View style={s.headingText}>
                <Text accessibilityRole="header" style={s.connectionTitle}>{heading}</Text>
                <Text style={s.caption}>{connected ? 'Connected via Bluetooth' : light.connecting ? 'Reading before control' : 'ZHIYUN · MOLUS G60'}</Text>
              </View>
            </View>
            {connected && hasValues && (
              <View style={s.power}>
                <Text style={s.powerLabel}>{actual.power === null ? 'Unknown' : actual.power ? 'On' : 'Off'}</Text>
                <View style={s.switchTarget}>
                  <Switch accessibilityLabel="Light output" accessibilityHint="Off sets brightness to zero. On restores the previous brightness."
                    hitSlop={8}
                    accessibilityState={{ disabled: !ready || actual.power === null }}
                    disabled={!ready || actual.power === null} value={actual.power === true}
                    onValueChange={power => perform(() => light.update({ power }))}
                    trackColor={{ false: color.line, true: color.accent }} thumbColor={actual.power ? color.ink : color.muted} />
                </View>
              </View>
            )}
          </View>

          {connected && (
            <View style={[s.statusRow, stacked && s.statusStacked]}>
              <View style={s.statusLine}>
                {light.working ? <ActivityIndicator color={color.accent} size="small" />
                  : <View accessible={false} style={[s.statusDot, { backgroundColor: light.verified ? color.success : color.error }]} />}
                <Text accessibilityLiveRegion="polite" style={[s.status, !light.verified && !light.working && s.error]}>
                  {light.working ? 'Checking readback…' : light.verified ? 'Readback verified' : 'Readback unverified'}
                </Text>
              </View>
              {readTime && <Text style={s.caption}>{light.verified ? 'Read' : 'Last read'} {readTime}</Text>}
            </View>
          )}

          {(!connected || issue || light.working) && (
            <View style={s.messageRow}>
              {!connected && busy && <ActivityIndicator color={color.accent} size="small" />}
              <Text accessibilityLiveRegion="polite" style={[s.message, issue && s.error]}>{message}</Text>
            </View>
          )}
          {connected && !light.verified && !light.working && <Text style={s.caption}>Read the light again to unlock controls. Displayed values are the last read.</Text>}

          <View style={s.actions}>
            {light.connecting ? (
              <Button label="Cancel connection" secondary onPress={() => perform(light.disconnect)} />
            ) : connected ? (
              <>
                <Button label={light.working ? 'Reading…' : 'Read light'} secondary={light.verified || light.working}
                  disabled={light.working} onPress={() => perform(light.refresh)} />
                <Button label="Disconnect" secondary onPress={() => perform(light.disconnect)} />
              </>
            ) : light.scanning ? (
              <Button label="Cancel search" secondary onPress={() => perform(light.stopScan)} />
            ) : light.permissionBlocked ? (
              <>
                <Button label="Open app settings" onPress={openSettings} />
                <Button label="Try again" secondary disabled={light.working || light.unsupported}
                  onPress={() => perform(light.scan)} />
              </>
            ) : !light.unsupported ? (
              <Button label={light.working ? 'Preparing Bluetooth…' : light.hasScanned ? 'Search again' : issue ? 'Try again' : 'Find my light'}
                secondary={light.devices.length > 0 && !issue} disabled={light.working}
                onPress={() => perform(light.scan)} />
            ) : null}
          </View>
        </View>

        {!connected && !light.connecting && light.devices.length > 0 && (
          <View style={s.nearby}>
            <Text accessibilityRole="header" accessibilityLiveRegion="polite" style={s.sectionTitle}>Nearby lights · {light.devices.length}</Text>
            <Text style={s.caption}>Choose the light you want to control.</Text>
            {light.devices.map(device => {
              const disabled = light.working || light.permissionBlocked || light.unsupported;
              return (
                <Pressable key={device.id} accessibilityRole="button"
                  accessibilityLabel={`Connect to ${deviceLabel(device.name)}. ${signalLabel(device.rssi)}.`}
                  accessibilityState={{ disabled }} disabled={disabled}
                  onPress={() => perform(() => light.connect(device))}
                  style={({ pressed }) => [s.nearbyLight, stacked && s.stacked, disabled && s.disabled, pressed && s.pressed]}>
                  <View style={s.nearbyText}>
                    <Text style={s.deviceName}>{deviceLabel(device.name)}</Text>
                    <View style={s.signalRow}><Signal rssi={device.rssi} /><Text style={s.caption}>{signalLabel(device.rssi)}</Text></View>
                  </View>
                  <View style={s.connectPill}><Text style={s.connectLabel}>Connect</Text></View>
                </Pressable>
              );
            })}
          </View>
        )}

        {connected && (
          <>
            <View accessibilityRole="tablist" style={s.destinations}>
              {(['controls', 'patterns'] as const).map(tab => (
                <Pressable key={tab} accessibilityRole="tab" accessibilityState={{ selected: destination === tab }}
                  onPress={() => setDestination(tab)} style={({ pressed }) => [s.destination, destination === tab && s.destinationSelected, pressed && s.pressed]}>
                  <Text style={[s.destinationLabel, destination === tab && s.destinationLabelSelected]}>
                    {tab === 'controls' ? 'Controls' : 'Patterns'}{tab === 'patterns' && light.effect.running ? ' · Running' : ''}
                  </Text>
                </Pressable>
              ))}
            </View>

            <View style={destination !== 'controls' && s.hidden} accessibilityElementsHidden={destination !== 'controls'}
              importantForAccessibility={destination === 'controls' ? 'auto' : 'no-hide-descendants'}>
              {hasValues ? (
                <View style={s.controls}>
                  {light.effect.running && (
                    <View style={s.patternNotice}>
                      <Text style={s.message}>A pattern is running. A manual adjustment stops it.</Text>
                      <Button label="View pattern" secondary onPress={() => setDestination('patterns')} />
                    </View>
                  )}
                  <IndicativeSlider kind="brightness" value={actual.brightness!} verified={light.verified}
                    working={light.working} disabled={!ready} onCommit={brightness => light.update({ brightness })} />
                  <IndicativeSlider kind="temperature" value={actual.cct!} verified={light.verified}
                    working={light.working} disabled={!ready} onCommit={cct => light.update({ cct })} />
                  <Text style={s.controlNote}>Off sets brightness to 0%. On restores the previous level. Disconnecting leaves the current output.</Text>
                </View>
              ) : (
                <View style={s.noReadback}>
                  <Text style={s.sectionTitle}>Waiting for actual values</Text>
                  <Text style={s.message}>Brightness and temperature will appear after readback. Use Read light to try again.</Text>
                </View>
              )}
            </View>

            {/* Keep the panel mounted across destinations so a tuned form is never discarded. */}
            <View style={destination !== 'patterns' && s.hidden} accessibilityElementsHidden={destination !== 'patterns'}
              importantForAccessibility={destination === 'patterns' ? 'auto' : 'no-hide-descendants'}>
              <View style={s.patternReadback}>
                <Text style={s.caption}>{light.verified ? 'Actual output · verified' : 'Last read · unverified'}</Text>
                <Text style={s.patternActual}>{actual.brightness === null ? '—' : `${Number(actual.brightness.toFixed(1))}%`}{'  ·  '}{actual.cct === null ? '—' : `${actual.cct} K`}</Text>
              </View>
              <PatternPanel status={light.effect} ready={ready} working={light.working} connected={connected}
                onStart={light.startEffect} onStop={light.stopEffect} />
            </View>
          </>
        )}

        {!connected && !light.connecting && !light.scanning && light.devices.length === 0 && !light.unsupported && !light.permissionBlocked && (
          <View style={s.discoveryHelp}>
            <Text style={s.sectionTitle}>{light.hasScanned ? 'Still looking?' : 'Before you start'}</Text>
            <Text style={s.message}>{light.hasScanned
              ? 'Keep your G60 close and disconnect it from any other control app, then search again.'
              : 'Switch on your G60 and keep it nearby. Allow Bluetooth access when Android asks.'}</Text>
            <Text style={s.caption}>Connect here in Lighttime. No pairing code or account needed.</Text>
          </View>
        )}

        <Pressable accessibilityRole="button" accessibilityState={{ expanded: showDetails }}
          onPress={() => setShowDetails(!showDetails)} style={s.detailsToggle}>
          <Text style={s.detailsLabel}>Connection details</Text>
          <Text accessible={false} style={s.detailsSymbol}>{showDetails ? '−' : '+'}</Text>
        </Pressable>
        {showDetails && (
          <View style={s.details}>
            <Text style={s.message}>Direct Bluetooth · no internet required.</Text>
            <Text style={s.caption}>Lighttime discovers the G60 under its Bluetooth name, PL103. If a search finds several lights, choose the one you want.</Text>
            {light.selected && <Text selectable style={s.caption}>Bluetooth name: {light.selected.name}{'\n'}Signal at discovery: {light.selected.rssi === null ? 'unavailable' : `${light.selected.rssi} dBm`}</Text>}
            {actual?.device && <Text selectable style={s.caption}>Model: {actual.device.model}{'\n'}Serial: {actual.device.serial}{'\n'}Device ID: {actual.device.id}</Text>}
            {light.unsupported && <Text selectable style={s.error}>{light.message}</Text>}
            <Text style={s.caption}>Brightness requests use whole percentages, and temperature uses 100 K steps. Actual values come from the light. A failed readback locks adjustments until Read light succeeds.</Text>
            <Text style={s.caption}>Backgrounding or disconnecting closes Bluetooth and stops this phone’s pattern, leaving the current output. Off means 0% brightness; it does not switch mains power. If first connected at 0%, On restores 5%.</Text>
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  );
}

export default function App() {
  return <SafeAreaProvider><LightScreen /></SafeAreaProvider>;
}

const s = StyleSheet.create({
  safe: { flex: 1, backgroundColor: color.background },
  page: { paddingHorizontal: 20, paddingTop: 14, paddingBottom: 28, maxWidth: 640, width: '100%', alignSelf: 'center' },
  header: { gap: 3, paddingBottom: 22 },
  brand: { color: color.text, fontSize: 25, fontWeight: '600', letterSpacing: -0.6 },
  headerNote: { color: color.muted, fontSize: 13 },
  connection: { backgroundColor: color.surface, borderRadius: 18, padding: 18, gap: 14, borderWidth: 1, borderColor: color.surface },
  connectionIssue: { borderColor: color.error },
  connectionHeading: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' },
  stacked: { flexDirection: 'column', alignItems: 'flex-start' },
  deviceHeading: { flexDirection: 'row', alignItems: 'center', gap: 12, flexShrink: 1, flexGrow: 1 },
  headingText: { gap: 5, flex: 1 },
  connectionTitle: { color: color.text, fontSize: 19, fontWeight: '600' },
  lightFace: { width: 34, height: 34, borderRadius: 17, borderWidth: 1, borderColor: color.line, backgroundColor: color.raised, alignItems: 'center', justifyContent: 'center' },
  lightFaceCenter: { width: 16, height: 16, borderRadius: 8, borderWidth: 1, borderColor: color.muted },
  lightFaceLit: { backgroundColor: color.background, borderColor: color.background, opacity: 0.6 },
  caption: { color: color.muted, fontSize: 13, flexShrink: 1 },
  power: { flexDirection: 'row', alignItems: 'center', gap: 4, marginLeft: 'auto' },
  powerLabel: { color: color.text, fontSize: 14 },
  switchTarget: { minHeight: minimumTouchTarget, minWidth: 56, alignItems: 'center', justifyContent: 'center' },
  statusRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  statusStacked: { flexDirection: 'column', alignItems: 'flex-start' },
  statusLine: { flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 1 },
  statusDot: { width: 7, height: 7, borderRadius: 4 },
  status: { color: color.text, fontSize: 13, flexShrink: 1 },
  messageRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  message: { color: color.muted, fontSize: 15, flexShrink: 1 },
  error: { color: color.error, fontSize: 14 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  nearby: { marginTop: 24, gap: 10 },
  sectionTitle: { fontSize: 16, fontWeight: '600', color: color.text },
  nearbyLight: { backgroundColor: color.surface, borderRadius: 14, padding: 16, minHeight: 88, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 14 },
  nearbyText: { gap: 9, flexShrink: 1 },
  deviceName: { fontSize: 17, fontWeight: '500', color: color.text },
  signalRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  signal: { flexDirection: 'row', alignItems: 'flex-end', gap: 3, height: 16 },
  signalBar: { width: 3, borderRadius: 1 },
  connectPill: { minHeight: minimumTouchTarget, minWidth: minimumTouchTarget, borderRadius: 10, paddingHorizontal: 16, paddingVertical: 13, backgroundColor: color.accent, alignItems: 'center', justifyContent: 'center' },
  connectLabel: { color: color.ink, fontSize: 15, fontWeight: '600' },
  destinations: { flexDirection: 'row', backgroundColor: color.surface, borderRadius: 13, padding: 4, marginTop: 20, marginBottom: 16, gap: 4 },
  destination: { minHeight: minimumTouchTarget, flex: 1, paddingVertical: 12, paddingHorizontal: 8, justifyContent: 'center', borderRadius: 10 },
  destinationSelected: { backgroundColor: color.raised },
  destinationLabel: { color: color.muted, fontSize: 15, textAlign: 'center' },
  destinationLabelSelected: { color: color.text, fontWeight: '600' },
  hidden: { display: 'none' },
  controls: { gap: 14 },
  controlNote: { color: color.muted, fontSize: 13, paddingHorizontal: 4, marginBottom: 4 },
  patternNotice: { borderWidth: 1, borderColor: color.line, borderRadius: 14, padding: 14, gap: 10 },
  patternReadback: { borderBottomWidth: 1, borderColor: color.line, paddingVertical: 10, gap: 7 },
  patternActual: { color: color.text, fontSize: 21, fontVariant: ['tabular-nums'] },
  noReadback: { padding: 18, gap: 10, backgroundColor: color.surface, borderRadius: 16 },
  discoveryHelp: { paddingVertical: 24, gap: 10, paddingHorizontal: 2 },
  detailsToggle: { marginTop: 20, borderTopWidth: 1, borderTopColor: color.line, minHeight: 56, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 16, gap: 12 },
  detailsLabel: { color: color.muted, fontSize: 14, flexShrink: 1 },
  detailsSymbol: { color: color.muted, fontSize: 20 },
  details: { gap: 15, paddingVertical: 8, paddingBottom: 16 },
  disabled: { opacity: 0.45 },
  pressed: { opacity: 0.7 },
});
