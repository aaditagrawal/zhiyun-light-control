import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import type { ComponentRef } from 'react';
import {
  ActivityIndicator, BackHandler, Linking, Platform, Pressable, ScrollView,
  StyleSheet, Text, useWindowDimensions, View,
} from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { useFonts } from 'expo-font';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { useLight } from './src/useLight';
import { PatternPanel } from './src/PatternPanel';
import { Button } from './src/ui/Button';
import { G60Artwork } from './src/ui/G60Artwork';
import { ShowcaseBoundary } from './src/showcase/ShowcaseBoundary';
import { IndicativeSlider } from './src/ui/IndicativeSlider';
import { canvas, color, font } from './src/ui/theme';
import { GlassChip, GlassCircle, ObsidianButton, StopGlyph, glass, jelly } from './src/pattern-ui/materials';
import { deviceLabel, SetupFlow, TextAction } from './src/setup/SetupFlow';
import type { SetupRoute } from './src/setup/SetupFlow';

type Destination = 'light' | 'scenes' | 'device';
const G60Showcase = lazy(() => import('./src/showcase/G60Showcase'));

const fonts = {
  [font.regular]: require('./assets/fonts/Poppins-Regular.ttf'),
  [font.medium]: require('./assets/fonts/Poppins-Medium.ttf'),
  [font.semibold]: require('./assets/fonts/Poppins-SemiBold.ttf'),
  [font.display]: require('./assets/fonts/PlayfairDisplay.ttf'),
};

const titles: Record<Destination, string> = { light: 'Light', scenes: 'Scenes', device: 'Device' };

/** Glossy red orb: lit when the G60 reads back on, dark glass when it reads back off. */
function PowerOrb({ on, disabled, onPress }: { on: boolean | null; disabled: boolean; onPress: () => void }) {
  return <Pressable accessibilityRole="switch" accessibilityLabel="Light output"
    accessibilityHint="Off sets brightness to zero. On restores the previous brightness."
    accessibilityState={{ checked: on === true, disabled }} disabled={disabled} onPress={onPress}
    style={({ pressed }) => [s.orbTarget, disabled && s.disabled, pressed && s.pressed]}>
    <View style={[s.orb, on ? s.orbOn : s.orbOff]}>
      <View style={s.powerRing}><View style={[s.powerGap, on ? s.powerGapOn : s.powerGapOff]} /></View>
      <View style={s.powerStem} />
    </View>
  </Pressable>;
}

function NavGlyph({ tab, active }: { tab: Destination; active: boolean }) {
  const tone = active ? color.text : color.muted;
  if (tab === 'light') return <View style={[s.glyphSun, { borderColor: tone }]}><View style={[s.glyphCore, { backgroundColor: tone }]} /></View>;
  if (tab === 'scenes') return <View style={s.glyphGrid}>{[0, 1, 2, 3].map(i => <View key={i} style={[s.glyphTile, { borderColor: tone }, i === 0 && { backgroundColor: tone }]} />)}</View>;
  return <View style={[s.glyphDevice, { borderColor: tone }]}><View style={[s.glyphLens, { backgroundColor: tone }]} /></View>;
}

function LightScreen() {
  const light = useLight();
  const actual = light.state;
  const connected = actual?.connected === true && !light.connecting;
  const ready = connected && light.verified && !light.working;
  const hasValues = connected && actual.brightness !== null && actual.cct !== null;
  const [route, setRoute] = useState<SetupRoute>('welcome');
  const [helpReturn, setHelpReturn] = useState<SetupRoute>('welcome');
  const [destination, setDestination] = useState<Destination>('light');
  const [mainOpened, setMainOpened] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [showcaseOpen, setShowcaseOpen] = useState(false);
  const [modelTouching, setModelTouching] = useState(false);
  const [modelOnscreen, setModelOnscreen] = useState(true);
  const lightHeroEnd = useRef(248);
  const [headerHeight, setHeaderHeight] = useState(64);
  const [navHeight, setNavHeight] = useState(68);
  const { width, fontScale } = useWindowDimensions();
  const compactNav = width < 360 || fontScale > 1.3;
  const wasConnected = useRef(false);
  const mainScroll = useRef<ComponentRef<typeof ScrollView>>(null);
  const readTime = actual?.updatedAt ? new Date(actual.updatedAt).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }) : null;

  const perform = (operation: () => Promise<unknown>) => {
    setActionError(null);
    void operation().catch(error => {
      setActionError(error instanceof Error ? error.message : 'That did not finish. Please try again.');
    });
  };

  const navigate = (next: SetupRoute) => {
    setActionError(null);
    if (next === 'help') setHelpReturn(route);
    // Leaving discovery cancels real work, including a pending permission request.
    perform(async () => {
      if (route === 'discovery' && next !== 'discovery' && next !== 'ready' && next !== 'main') {
        if (light.connecting) await light.disconnect();
        else if (light.scanning || light.working) await light.stopScan();
      }
      setRoute(next);
    });
  };

  const goBack = () => {
    if (route === 'help') { navigate(connected ? 'main' : helpReturn); return; }
    if (route === 'ready') { navigate('main'); return; }
    if (route === 'discovery') { navigate('access'); return; }
    if (route === 'access') { navigate('prepare'); return; }
    if (route === 'prepare') { navigate('welcome'); return; }
    if (route === 'main') { setHelpOpen(false); setDestination('light'); }
  };

  useEffect(() => {
    if (connected && !wasConnected.current) {
      setRoute(light.verified ? 'ready' : 'main'); setDestination('light');
    } else if (!connected && wasConnected.current) {
      setRoute('discovery');
    }
    wasConnected.current = connected;
  }, [connected]);

  useEffect(() => {
    if (Platform.OS !== 'android') return;
    const handler = BackHandler.addEventListener('hardwareBackPress', () => {
      if (route === 'main' && helpOpen) { setHelpOpen(false); return true; }
      if (route === 'welcome' || (route === 'main' && destination === 'light')) return false;
      goBack(); return true;
    });
    return () => handler.remove();
  });

  useEffect(() => {
    mainScroll.current?.scrollTo({ y: 0, animated: false }); setHelpOpen(false);
    setModelTouching(false); setModelOnscreen(true);
  }, [destination]);
  useEffect(() => { setModelTouching(false); }, [route, showcaseOpen]);
  useEffect(() => { if (route === 'main') setMainOpened(true); }, [route]);

  const search = () => {
    setRoute('discovery');
    perform(light.scan);
  };
  const openAppSettings = () => perform(async () => {
    try { await Linking.openSettings(); }
    catch { throw new Error(`Open Android Settings → Apps → Lighttime → Permissions and allow ${Number(Platform.Version) >= 31 ? 'Nearby devices' : 'Location'}.${Number(Platform.Version) >= 31 ? '' : ' Turn Android Location on for discovery.'}`); }
  });
  const openBluetoothSettings = () => perform(async () => {
    try {
      if (Platform.OS === 'android') await Linking.sendIntent('android.settings.BLUETOOTH_SETTINGS');
      else await Linking.openSettings();
    } catch { throw new Error('Swipe down from the top of your phone and turn Bluetooth on in Android quick settings.'); }
  });
  const actualLabel = light.working ? 'Checking actual output…' : light.verified ? 'Readback verified' : 'Last read · unverified';
  const chipLabel = light.working ? 'Checking' : light.verified ? 'Verified' : 'Unverified';
  const chipDot = light.working ? color.muted : light.verified ? '#FF4A4A' : color.error;
  const issue = actionError ?? (light.error && !light.working ? light.message : null);

  return <SafeAreaView style={[s.safe, canvas]}>
    <StatusBar style="light" />
    {route !== 'main' && <SetupFlow key={route} route={route} light={light} actionError={actionError}
      onNavigate={navigate} onBack={goBack} onSearch={search}
      onCancel={() => perform(light.connecting ? light.disconnect : light.stopScan)}
      onConnect={device => perform(() => light.connect(device))}
      onAppSettings={openAppSettings} onBluetoothSettings={openBluetoothSettings}
      modelActive={!showcaseOpen} onExploreModel={() => setShowcaseOpen(true)} />}

    {/* Open the instruments on first use, then retain them across navigation. */}
    {(route === 'main' || mainOpened) && <View style={[s.main, route !== 'main' && s.hidden]} accessibilityElementsHidden={route !== 'main'}
      importantForAccessibility={route === 'main' ? 'auto' : 'no-hide-descendants'}>
      <View style={s.headerOverlay} onLayout={event => setHeaderHeight(event.nativeEvent.layout.height)}>
      <View pointerEvents="none" style={s.headerFade} />
      <View style={s.topbar}>
        <GlassCircle size={48} label={destination === 'device' ? 'Connection help' : 'Help'} selected={destination === 'device' ? undefined : helpOpen}
          onPress={() => destination === 'device' ? navigate('help') : setHelpOpen(open => !open)}>
          <Text accessible={false} allowFontScaling={false} style={s.circleGlyph}>?</Text>
        </GlassCircle>
        <Text accessibilityRole="header" numberOfLines={1} maxFontSizeMultiplier={1.4} style={s.title}>{titles[destination]}</Text>
        {destination === 'device' ? <View accessible accessibilityLabel={connected ? 'Connected' : 'Offline'} style={[s.connectionIndicator, glass]}>
          <View style={[s.linkDot, !connected && s.offlineDot]} />
        </View> : <GlassCircle size={48} label={connected ? 'View connected device' : 'View device, offline'} onPress={() => setDestination('device')}>
          <View style={[s.linkDot, !connected && s.offlineDot]} />
        </GlassCircle>}
      </View>
      {connected && light.effect.running && <View style={[s.runningBar, glass]}>
        <View style={s.runningPulse} />
        <Pressable accessibilityRole="button" accessibilityLabel={`Scene running.${destination === 'light' ? ' A manual adjustment stops it.' : ''} View running scene.`}
          onPress={() => setDestination('scenes')} style={s.runningCopy}>
          <Text accessibilityLiveRegion="polite" numberOfLines={1} style={s.runningTitle}>Scene running</Text>
          {destination === 'light' && <Text numberOfLines={1} style={s.runningNote}>Adjusting stops it</Text>}
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Stop running scene and restore light"
          accessibilityHint="Stop the scene and restore the brightness and white balance captured at Start."
          accessibilityState={{ disabled: light.working }} disabled={light.working}
          android_ripple={{ color: 'rgba(255,255,255,0.12)' }} onPress={() => perform(light.stopEffect)}
          style={({ pressed }) => [s.runningStop, light.working && s.disabled, pressed && s.pressed]}>
          <StopGlyph size={11} /><Text style={s.runningStopLabel}>Stop & restore</Text>
        </Pressable>
      </View>}
      </View>
      <ScrollView ref={mainScroll} style={s.scroller} removeClippedSubviews={false}
        scrollEnabled={!modelTouching} scrollEventThrottle={64}
        onScroll={event => setModelOnscreen(event.nativeEvent.contentOffset.y < (destination === 'light' ? lightHeroEnd.current : 256))}
        contentContainerStyle={[s.page, { paddingTop: headerHeight + 24, paddingBottom: navHeight + 60 }]}
        keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag">
        <View style={[s.destination, destination !== 'light' && s.hidden]} accessibilityElementsHidden={destination !== 'light'}
          importantForAccessibility={destination === 'light' ? 'auto' : 'no-hide-descendants'}>
          {helpOpen && destination === 'light' && <View style={[s.help, glass]}>
            <Text style={s.helpText}>Values are read back from your G60. Off sets brightness to 0%; mains stays on.</Text>
            <TextAction label="Connection help" onPress={() => navigate('help')} />
          </View>}
          {issue && <View style={[s.issue, glass]}>
            <Text accessibilityLiveRegion="polite" style={s.error}>{issue}</Text>
            {connected && !light.verified && <Button label="Read light again" secondary disabled={light.working} onPress={() => perform(light.refresh)} />}
          </View>}
          <View style={s.stage} onLayout={event => { lightHeroEnd.current = event.nativeEvent.layout.y + 248; }}>
            <View accessible={false} pointerEvents="none" style={s.deckBack} />
            <View accessible={false} pointerEvents="none" style={s.deckMid} />
            <View style={s.product}><G60Artwork control interactive={route === 'main' && destination === 'light'}
              active={!showcaseOpen && modelOnscreen} onInteractionChange={setModelTouching}
              onExplore={() => setShowcaseOpen(true)} /></View>
            <View style={[s.panel, jelly('ruby')]}>
              <View style={s.panelCorners}>
                <View accessible accessibilityLabel={`${actualLabel}${readTime ? ` at ${readTime}` : ''}`} style={s.chipWrap}>
                  {light.working ? <View style={[s.workingChip, glass]}><ActivityIndicator size="small" color={color.text} /><Text style={s.workingLabel}>{chipLabel}</Text></View>
                    : <GlassChip label={chipLabel} dot={chipDot} />}
                </View>
                <PowerOrb on={actual?.power ?? null} disabled={!ready || actual?.power == null}
                  onPress={() => perform(() => light.update({ power: actual?.power !== true }))} />
              </View>
              {hasValues ? <IndicativeSlider kind="brightness" value={actual.brightness!} verified={light.verified}
                working={light.working} disabled={!ready} onCommit={brightness => light.update({ brightness })} />
                : <View style={s.waiting}><Text style={s.waitingTitle}>Waiting for your light</Text>
                  <Button label="Read light" disabled={!connected || light.working} onPress={() => perform(light.refresh)} /></View>}
            </View>
          </View>
          {hasValues && <View style={[s.panel, s.cctPanel, jelly('night', 0.9)]}>
            <IndicativeSlider kind="temperature" value={actual.cct!} verified={light.verified}
              working={light.working} disabled={!ready} onCommit={cct => light.update({ cct })} />
          </View>}
        </View>

        <View style={[s.destination, destination !== 'scenes' && s.hidden]} accessibilityElementsHidden={destination !== 'scenes'}
          importantForAccessibility={destination === 'scenes' ? 'auto' : 'no-hide-descendants'}>
          {helpOpen && destination === 'scenes' && <View style={[s.help, glass]}>
            <Text style={s.helpText}>A scene runs from this phone. Stop & restore returns the output captured at Start.</Text>
          </View>}
          <View accessible accessibilityLabel={`${actualLabel}. ${hasValues ? `${actual.brightness}% and ${actual.cct} K` : 'No actual values yet'}`} style={s.sceneReadback}>
            <GlassChip label={hasValues ? `${actual.brightness}% · ${actual.cct} K` : '—'} dot={chipDot} />
          </View>
          {connected && !light.verified && !light.working && <View style={[s.issue, glass]}>
            <Text accessibilityLiveRegion="polite" style={s.error}>{actionError ?? (light.error ? light.message : 'Read the light again to verify its current output.')}</Text>
            <Button label="Read light again" secondary onPress={() => perform(light.refresh)} />
          </View>}
          <PatternPanel status={light.effect} ready={ready} working={light.working} connected={connected}
            onStart={light.startEffect} onStop={light.stopEffect} />
        </View>

        <View style={[s.destination, destination !== 'device' && s.hidden]} accessibilityElementsHidden={destination !== 'device'}
          importantForAccessibility={destination === 'device' ? 'auto' : 'no-hide-descendants'}>
          <View style={s.profile}>
            <View style={[s.profileHalo, jelly('ruby', 0.8)]}><View style={s.profileProduct}><G60Artwork
              interactive={route === 'main' && destination === 'device'} active={!showcaseOpen && modelOnscreen}
              onInteractionChange={setModelTouching} onExplore={() => setShowcaseOpen(true)} /></View></View>
            <Text style={s.deviceTitle}>{deviceLabel(light.selected?.name)}</Text>
            <View style={s.centerChip}><GlassChip label={connected ? 'Connected' : 'Offline'} dot={connected ? '#FF4A4A' : color.muted} /></View>
          </View>
          <View style={[s.stats, compactNav && s.statsStack, jelly('night', 0.85)]}>
            {[['Brightness', actual?.brightness == null ? '—' : `${actual.brightness}%`],
              ['White', actual?.cct == null ? '—' : `${actual.cct} K`],
              ['Read', readTime ?? '—']].map(([label, value]) => <View key={label} accessible accessibilityLabel={`${label}: ${value}`} style={[s.stat, compactNav && s.statRow]}>
              <Text numberOfLines={1} adjustsFontSizeToFit maxFontSizeMultiplier={1.5} style={s.statValue}>{value}</Text>
              <Text style={s.statLabel}>{label}</Text>
            </View>)}
          </View>
          <View style={s.deviceActions}>
            <Button label={light.working ? 'Reading…' : light.verified ? 'Read again' : 'Read light'} secondary disabled={light.working || !connected} onPress={() => perform(light.refresh)} />
            {!light.verified && connected && !light.working && <Text style={s.small}>Read to verify and unlock controls.</Text>}
          </View>
          {issue && <Text accessibilityLiveRegion="polite" style={s.error}>{issue}</Text>}
          {(light.selected || actual?.device) && <View style={[s.details, glass]}>
            {light.selected && <View style={s.detailRow}><Text style={s.detailLabel}>Bluetooth</Text>
              <Text selectable numberOfLines={1} style={s.detailValue}>{light.selected.name}{light.selected.rssi === null ? '' : ` · ${light.selected.rssi} dBm`}</Text></View>}
            {actual?.device && <>
              <View style={s.detailRow}><Text style={s.detailLabel}>Model</Text><Text selectable style={s.detailValue}>{actual.device.model}</Text></View>
              <View style={s.detailRow}><Text style={s.detailLabel}>Serial</Text><Text selectable style={s.detailValue}>{actual.device.serial}</Text></View>
              <View style={s.detailRow}><Text style={s.detailLabel}>ID</Text><Text selectable numberOfLines={1} style={s.detailValue}>{actual.device.id}</Text></View>
            </>}
          </View>}
          <ObsidianButton label="Disconnect" hint="Stops this phone’s scene. Your light keeps its current output."
            onPress={() => perform(light.disconnect)} />
        </View>
      </ScrollView>
      <View pointerEvents="none" style={[s.bottomFade, { height: navHeight + 70 }]} />
      <View accessibilityRole="tablist" style={s.nav} onLayout={event => setNavHeight(event.nativeEvent.layout.height)}>
        {(['light', 'scenes', 'device'] as const).map(tab => <Pressable key={tab} accessibilityRole="tab"
          accessibilityState={{ selected: destination === tab }} accessibilityLabel={`${titles[tab]}${tab === 'scenes' && light.effect.running ? ', running' : ''}`}
          android_ripple={{ color: 'rgba(255,255,255,0.1)', borderless: true }} onPress={() => setDestination(tab)}
          style={({ pressed }) => [s.navItem, destination === tab && s.navSelected, pressed && s.pressed]}>
          <NavGlyph tab={tab} active={destination === tab} />
          {destination === tab && !compactNav && <Text numberOfLines={1} maxFontSizeMultiplier={1.3} style={s.navLabel}>{titles[tab]}</Text>}
          {tab === 'scenes' && light.effect.running && <View style={s.navLive} />}
        </Pressable>)}
      </View>
    </View>}
    {showcaseOpen && <ShowcaseBoundary onClose={() => setShowcaseOpen(false)}>
      <Suspense fallback={null}><G60Showcase onClose={() => setShowcaseOpen(false)}
        running={light.effect.running} working={light.working} sceneError={light.effect.lastError}
        onStop={light.stopEffect} /></Suspense>
    </ShowcaseBoundary>}
  </SafeAreaView>;
}

export default function App() {
  const [loaded, error] = useFonts(fonts);
  // Hold the lit canvas briefly so text never flashes in a fallback face; a font error still renders.
  if (!loaded && !error) return <View style={[s.safe, canvas]} />;
  return <SafeAreaProvider><LightScreen /></SafeAreaProvider>;
}

const s = StyleSheet.create({
  safe: { flex: 1 }, main: { flex: 1 }, hidden: { display: 'none' },
  scroller: { flex: 1 },
  headerOverlay: { position: 'absolute', top: 0, left: 0, right: 0, zIndex: 3 },
  headerFade: { position: 'absolute', top: 0, bottom: -36, left: 0, right: 0,
    backgroundImage: 'linear-gradient(180deg, rgba(81, 9, 9, 0.98) 0%, rgba(81, 9, 9, 0.96) 54%, rgba(81, 9, 9, 0.76) 70%, rgba(81, 9, 9, 0.24) 87%, rgba(81, 9, 9, 0) 100%)' },
  bottomFade: { position: 'absolute', bottom: 0, left: 0, right: 0, zIndex: 1,
    backgroundImage: 'linear-gradient(180deg, rgba(21, 5, 5, 0) 0%, rgba(21, 5, 5, 0.08) 20%, rgba(21, 5, 5, 0.38) 42%, rgba(21, 5, 5, 0.8) 68%, rgba(21, 5, 5, 0.98) 100%)' },
  connectionIndicator: { width: 48, height: 48, borderRadius: 24, alignItems: 'center', justifyContent: 'center' },
  topbar: { minHeight: 64, paddingHorizontal: 20, paddingVertical: 8, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    gap: 12, maxWidth: 600, width: '100%', alignSelf: 'center' },
  title: { flex: 1, color: color.text, fontFamily: font.medium, fontSize: 18, lineHeight: 26, textAlign: 'center' },
  circleGlyph: { color: color.text, fontFamily: font.medium, fontSize: 18, lineHeight: 24 },
  linkDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: '#FF4A4A', boxShadow: '0 0 10px rgba(255, 60, 60, 0.95)' },
  offlineDot: { backgroundColor: 'rgba(242, 210, 209, 0.45)', boxShadow: 'none' },
  runningBar: { marginHorizontal: 20, marginBottom: 6, paddingLeft: 18, paddingRight: 5, paddingVertical: 5, borderRadius: 32,
    flexDirection: 'row', alignItems: 'center', gap: 10, flexWrap: 'wrap', maxWidth: 560, alignSelf: 'stretch' },
  runningPulse: { width: 9, height: 9, borderRadius: 5, backgroundColor: '#FF4A4A', boxShadow: '0 0 0 4px rgba(255, 60, 60, 0.25), 0 0 12px rgba(255, 60, 60, 0.9)' },
  runningCopy: { flex: 1, minWidth: 110, minHeight: 48, justifyContent: 'center' },
  runningTitle: { color: color.text, fontFamily: font.medium, fontSize: 14, lineHeight: 20 },
  runningNote: { color: color.muted, fontFamily: font.regular, fontSize: 13, lineHeight: 18 },
  runningStop: { minHeight: 48, paddingHorizontal: 18, borderRadius: 24, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 9,
    backgroundColor: '#050404', backgroundImage: 'radial-gradient(ellipse 30% 95% at 15% 20%, rgba(255, 255, 255, 0.3) 0%, rgba(255, 255, 255, 0) 100%), radial-gradient(ellipse 22% 80% at 89% 58%, rgba(255, 255, 255, 0.16) 0%, rgba(255, 255, 255, 0) 100%)',
    boxShadow: 'inset 0 1px 0 rgba(255, 255, 255, 0.26), 0 10px 20px -10px rgba(0, 0, 0, 0.9)' },
  runningStopLabel: { color: color.text, fontFamily: font.medium, fontSize: 14, lineHeight: 20, textAlign: 'center' },
  page: { paddingHorizontal: 20, paddingTop: 16, paddingBottom: 36, width: '100%', maxWidth: 600, alignSelf: 'center' },
  destination: { gap: 28 },
  help: { borderRadius: 28, padding: 18, gap: 4 },
  helpText: { color: color.text, fontFamily: font.regular, fontSize: 15, lineHeight: 23 },
  stage: { paddingTop: 0 },
  deckBack: { position: 'absolute', top: 0, left: 44, right: 44, height: 160, borderRadius: 34,
    backgroundColor: '#6E1515', backgroundImage: 'linear-gradient(180deg, rgba(255, 170, 170, 0.3) 0%, rgba(255, 170, 170, 0) 55%)',
    boxShadow: 'inset 0 1.5px 0 rgba(255, 220, 220, 0.45), 0 10px 24px rgba(0, 0, 0, 0.4)' },
  deckMid: { position: 'absolute', top: 16, left: 22, right: 22, height: 160, borderRadius: 36,
    backgroundColor: '#852222', backgroundImage: 'radial-gradient(ellipse 70% 90% at 50% 0%, rgba(255, 120, 110, 0.55) 0%, rgba(255, 120, 110, 0) 100%)',
    boxShadow: 'inset 0 1.5px 0 rgba(255, 220, 220, 0.6), 0 12px 28px rgba(0, 0, 0, 0.45)' },
  product: { height: 212, marginTop: 12, marginBottom: 12, zIndex: 1, overflow: 'visible' },
  panel: { borderRadius: 36, paddingHorizontal: 20, paddingTop: 20, paddingBottom: 24 },
  panelCorners: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 },
  chipWrap: { paddingTop: 8 },
  workingChip: { minHeight: 30, borderRadius: 15, paddingHorizontal: 10, flexDirection: 'row', alignItems: 'center', gap: 6 },
  workingLabel: { color: color.text, fontFamily: font.medium, fontSize: 13, lineHeight: 18 },
  cctPanel: { paddingTop: 24, paddingBottom: 24, borderRadius: 34 },
  orbTarget: { width: 56, height: 56, alignItems: 'center', justifyContent: 'center' },
  orb: { width: 50, height: 50, borderRadius: 25, alignItems: 'center', justifyContent: 'center' },
  orbOn: { backgroundColor: '#C22422',
    backgroundImage: 'radial-gradient(circle at 36% 28%, rgba(255, 220, 220, 0.95) 0%, rgba(255, 90, 90, 0.9) 22%, rgba(194, 20, 20, 1) 58%, rgba(70, 4, 4, 1) 100%)',
    boxShadow: '0 0 22px 4px rgba(255, 40, 40, 0.7), inset 0 -4px 8px rgba(0, 0, 0, 0.45), 0 0 0 1.5px rgba(255, 190, 190, 0.6)' },
  orbOff: { backgroundColor: '#1A0404',
    backgroundImage: 'radial-gradient(circle at 36% 28%, rgba(255, 200, 200, 0.28) 0%, rgba(90, 14, 14, 0.9) 40%, rgba(16, 2, 2, 1) 100%)',
    boxShadow: 'inset 0 1px 0 rgba(255, 220, 220, 0.4), inset 0 -4px 8px rgba(0, 0, 0, 0.6), 0 6px 14px rgba(0, 0, 0, 0.5)' },
  powerRing: { width: 20, height: 20, borderRadius: 10, borderWidth: 2.5, borderColor: color.text, alignItems: 'center' },
  powerGap: { width: 8, height: 6, marginTop: -3 },
  powerGapOn: { backgroundColor: '#E04040' }, powerGapOff: { backgroundColor: '#3A0808' },
  powerStem: { position: 'absolute', top: 13, width: 2.5, height: 11, borderRadius: 1.25, backgroundColor: color.text },
  waiting: { minHeight: 260, alignItems: 'center', justifyContent: 'center', gap: 18, paddingTop: 40 },
  waitingTitle: { color: color.text, fontFamily: font.medium, fontSize: 18, lineHeight: 26, textAlign: 'center' },
  issue: { borderRadius: 28, padding: 16, gap: 12 },
  error: { color: color.error, fontFamily: font.regular, fontSize: 14, lineHeight: 21 },
  sceneReadback: { alignItems: 'center' },
  small: { color: color.muted, fontFamily: font.regular, fontSize: 13, lineHeight: 19, textAlign: 'center' },
  profile: { alignItems: 'center', gap: 20, paddingTop: 12 },
  profileHalo: { width: 220, height: 220, borderRadius: 110, alignItems: 'center', justifyContent: 'center' },
  profileProduct: { width: 270, marginTop: 6 },
  centerChip: { alignSelf: 'center' },
  deviceTitle: { color: color.text, fontFamily: font.display, fontVariant: ['lining-nums'], fontSize: 28, lineHeight: 36, textAlign: 'center', paddingHorizontal: 12 },
  stats: { flexDirection: 'row', borderRadius: 30, paddingVertical: 24, paddingHorizontal: 8 },
  statsStack: { flexDirection: 'column', paddingHorizontal: 24, gap: 12 },
  stat: { flexGrow: 1, flexShrink: 1, flexBasis: 0, alignItems: 'center', gap: 2, paddingHorizontal: 4 },
  statRow: { flexGrow: 0, flexShrink: 0, flexBasis: 'auto', minHeight: 48,
    flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', columnGap: 16, rowGap: 6 },
  statValue: { color: color.text, fontFamily: font.semibold, fontSize: 18, lineHeight: 26, fontVariant: ['tabular-nums'] },
  statLabel: { color: color.muted, fontFamily: font.regular, fontSize: 13, lineHeight: 18 },
  deviceActions: { gap: 8 },
  details: { borderRadius: 28, paddingVertical: 12, paddingHorizontal: 18 },
  detailRow: { minHeight: 44, flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', alignContent: 'center', justifyContent: 'space-between', gap: 10 },
  detailLabel: { color: color.muted, fontFamily: font.regular, fontSize: 14, lineHeight: 20 },
  detailValue: { color: color.text, fontFamily: font.medium, fontSize: 14, lineHeight: 20, flexShrink: 1, textAlign: 'right' },
  nav: { position: 'absolute', bottom: 10, left: 20, right: 20, zIndex: 2,
    flexDirection: 'row', gap: 6, padding: 6, borderRadius: 36,
    maxWidth: 560, alignSelf: 'stretch', backgroundColor: '#2D1A18',
    backgroundImage: 'linear-gradient(180deg, rgba(255, 220, 210, 0.1) 0%, rgba(255, 220, 210, 0) 60%)',
    boxShadow: 'inset 0 1px 0 rgba(255, 220, 210, 0.22), inset 0 0 0 1px rgba(255, 200, 190, 0.08), 0 14px 30px -8px rgba(0, 0, 0, 0.7)' },
  navItem: { minHeight: 56, flex: 1, borderRadius: 28, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingHorizontal: 8 },
  navSelected: { flex: 1.6, borderWidth: 1.5, borderColor: '#A06E43', backgroundColor: 'rgba(20, 6, 5, 0.6)',
    backgroundImage: 'radial-gradient(ellipse 60% 120% at 50% 100%, rgba(255, 50, 50, 0.38) 0%, rgba(255, 50, 50, 0) 100%)',
    boxShadow: '0 0 14px rgba(160, 110, 67, 0.35), inset 0 1px 0 rgba(255, 220, 190, 0.25)' },
  navLabel: { color: color.text, fontFamily: font.medium, fontSize: 14, lineHeight: 20, flexShrink: 1 },
  navLive: { position: 'absolute', top: 10, right: '30%', width: 7, height: 7, borderRadius: 4, backgroundColor: '#FF4A4A', boxShadow: '0 0 8px rgba(255, 60, 60, 0.95)' },
  glyphSun: { width: 20, height: 20, borderRadius: 10, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  glyphCore: { width: 8, height: 8, borderRadius: 4 },
  glyphGrid: { width: 20, height: 20, flexDirection: 'row', flexWrap: 'wrap', gap: 2 },
  glyphTile: { width: 9, height: 9, borderRadius: 3, borderWidth: 1.75 },
  glyphDevice: { width: 22, height: 16, borderRadius: 5, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  glyphLens: { width: 10, height: 4, borderRadius: 2 },
  disabled: { opacity: 0.42 }, pressed: { opacity: 0.78 },
});
