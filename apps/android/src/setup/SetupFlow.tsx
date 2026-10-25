import { ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { useLight } from '../useLight';
import type { DiscoveredLight } from '../ble/constants';
import { G60Artwork } from '../ui/G60Artwork';
import { Button } from '../ui/Button';
import { GlassCircle, NeonWell, ObsidianButton, Rim, glass, jelly } from '../pattern-ui/materials';

export type SetupRoute = 'welcome' | 'prepare' | 'access' | 'discovery' | 'help' | 'ready' | 'main';
type Light = ReturnType<typeof useLight>;
const face = { regular: 'Poppins-Regular', medium: 'Poppins-Medium', semibold: 'Poppins-SemiBold', display: 'PlayfairDisplay' };

export function deviceLabel(name?: string): string {
  const suffix = name?.replace(/^PL103[\s_-]*/i, '').trim();
  return suffix ? 'MOLUS G60 · ' + suffix : 'MOLUS G60';
}
function signalLabel(rssi: number | null): string {
  return rssi === null ? 'Signal unavailable' : rssi >= -60 ? 'Strong signal' : rssi >= -75 ? 'Good signal' : 'Weak signal · move closer';
}
export function TextAction({ label, onPress }: { label: string; onPress: () => void }) {
  return <Pressable accessibilityRole="button" onPress={onPress}
    style={({ pressed }) => [s.textAction, pressed && s.pressed]}><Text style={s.textActionLabel}>{label}</Text></Pressable>;
}
function Step({ number, title, children }: { number: string; title: string; children: string }) {
  return <View style={[s.step, glass]}>
    <View accessible={false} style={s.stepDisc}><Text allowFontScaling={false} style={s.stepNumber}>{number}</Text></View>
    <View style={s.stepCopy}><Text style={s.stepTitle}>{title}</Text><Text style={s.body}>{children}</Text></View>
  </View>;
}
function BluetoothMark() {
  return <View accessible={false} importantForAccessibility="no-hide-descendants" style={s.bluetoothMark}>
    <View style={[s.bluetoothLine, s.bluetoothStem]} /><View style={[s.bluetoothLine, s.bluetoothTop]} />
    <View style={[s.bluetoothLine, s.bluetoothBottom]} /><View style={[s.bluetoothLine, s.bluetoothCrossUp]} />
    <View style={[s.bluetoothLine, s.bluetoothCrossDown]} />
  </View>;
}
function Signal({ rssi }: { rssi: number | null }) {
  const level = rssi === null ? 0 : rssi >= -60 ? 4 : rssi >= -75 ? 3 : 2;
  return <View accessible={false} style={s.signal}>{[0, 1, 2, 3].map(i => <View key={i}
    style={[s.signalBar, { height: 5 + i * 4, opacity: i < level ? 1 : 0.24 }]} />)}</View>;
}
function ProductHalo({ scanning = false }: { scanning?: boolean }) {
  return <View accessible={false} importantForAccessibility="no-hide-descendants" style={s.haloStage}>
    <View style={s.haloCircle}><NeonWell size={226} lit={scanning}><View /></NeonWell></View>
    <View style={s.haloProduct}><G60Artwork compact /></View>
  </View>;
}

export function SetupFlow({ route, light, actionError, onNavigate, onBack, onSearch, onCancel, onConnect, onAppSettings, onBluetoothSettings, onExploreModel }: {
  route: SetupRoute; light: Light; actionError: string | null;
  onNavigate: (route: SetupRoute) => void; onBack: () => void; onSearch: () => void; onCancel: () => void;
  onConnect: (device: DiscoveredLight) => void; onAppSettings: () => void; onBluetoothSettings: () => void;
  onExploreModel?: () => void;
}) {
  const welcome = route === 'welcome', discovery = route === 'discovery', connecting = light.connecting;
  const off = light.status === 'Bluetooth is off';
  const denied = !light.working && light.status === 'Bluetooth permission needed';
  const blocked = light.permissionBlocked;
  const modern = Number(Platform.Version) >= 31;
  const permission = modern ? 'Nearby devices' : 'Location';
  const error = !!actionError || (light.error && !light.working && !denied && !off);
  const hasResults = light.devices.length > 0 && !off && !blocked && !denied && !light.unsupported && !connecting;
  const step = route === 'prepare' ? 1 : route === 'access' ? 2 : discovery ? 3 : null;
  const scanLabel = light.scanning ? 'Searching nearby' : connecting ? 'Reading your light' : light.working ? 'Waiting for access'
    : light.unsupported ? 'Unavailable' : off ? 'Bluetooth off' : blocked || denied ? 'Access needed'
      : error ? 'Connection interrupted' : hasResults ? light.devices.length + (light.devices.length === 1 ? ' light found' : ' lights found')
        : light.hasScanned ? 'No lights found' : 'Ready to discover';
  const discoveryTitle = connecting ? 'Connecting.' : off ? 'Turn Bluetooth on.' : blocked || denied ? 'Allow access.'
    : light.unsupported ? 'Bluetooth unavailable.' : error ? 'Try again.' : hasResults ? 'Choose your G60.'
      : light.scanning ? 'Finding your light.' : light.hasScanned ? 'Nothing nearby. Yet.' : 'Find your light.';
  const activeMessage = actionError ?? light.message.replace(/\bPL103\b/g, 'G60');
  const recovery = actionError ?? (off ? 'Turn Bluetooth on, then search again.'
    : blocked ? 'Allow ' + permission + ' in Lighttime’s app settings.' + (modern ? '' : ' Keep Android Location on.')
      : denied ? 'Allow ' + permission + ' to find and connect to your G60.'
        : error ? activeMessage : light.unsupported ? 'Use the standalone app on a Bluetooth-capable Android phone.'
          : light.hasScanned && !light.scanning && !hasResults && !connecting ? 'Power on your G60 and disconnect other controllers.' : null);
  const title = welcome ? 'lighttime' : route === 'help' ? 'Connection help' : route === 'ready' ? 'Connected' : discovery ? 'Discover' : 'Set up your G60';

  return <View style={s.shell}>
    <ScrollView removeClippedSubviews={false} contentContainerStyle={[s.page, welcome && s.welcomePage, route === 'ready' && s.readyPage]} keyboardShouldPersistTaps="handled">
      <View style={[s.topbar, welcome && s.welcomeTopbar]}>
        {welcome ? <View accessible={false} style={s.brandOrb}><View style={s.brandCore} /></View>
          : <GlassCircle size={48} label={connecting ? 'Cancel connection and go back' : 'Go back'} onPress={onBack}>
            <View accessible={false} style={s.backChevron} />
          </GlassCircle>}
        <Text style={s.barTitle}>{title}</Text>
        {route === 'help' ? <View style={s.barSpacer} />
          : <GlassCircle size={48} label="Connection help" onPress={() => onNavigate('help')}>
            <Text accessible={false} allowFontScaling={false} style={s.helpGlyph}>?</Text>
          </GlassCircle>}
      </View>
      {step !== null && <View accessible accessibilityLabel={'Setup step ' + step + ' of 3'} style={s.progress}>
        {[1, 2, 3].map(index => <View key={index} style={[s.progressPart, index <= step && s.progressActive]} />)}
      </View>}

      {welcome && <>
        <View style={s.welcomeHero}>
          <View pointerEvents="none" style={s.heroGlow} />
          <View style={s.welcomeProduct}><G60Artwork onExplore={onExploreModel} /></View>
          <Text style={s.productName}>MOLUS G60</Text>
        </View>
        <View style={[s.welcomeSheet, jelly('ruby', 0.7)]}>
          <Text accessibilityRole="header" style={s.welcomeHeading}>Set the light.{'\n'}<Text style={s.pink}>Feel the moment.</Text></Text>
          <ObsidianButton label="Set up my G60" hint="Start guided Bluetooth setup" onPress={() => onNavigate('prepare')} />
          <TextAction label="Find my light" onPress={onSearch} />
        </View>
      </>}

      {route === 'prepare' && <>
        <View style={s.titleBlock}><Text accessibilityRole="header" style={s.heading}>A little preparation.</Text></View>
        <ProductHalo />
        <View style={s.steps}>
          <Step number="01" title="Power on">Connect the G60’s power supply and switch it on.</Step>
          <Step number="02" title="Free the connection">Disconnect ZY Vega or any other controller.</Step>
        </View>
        <ObsidianButton label="My light is on" onPress={() => onNavigate('access')} />
        <Text style={s.caption}>Connect here. No Android pairing code.</Text>
      </>}

      {route === 'access' && <>
        <View style={s.titleBlock}><Text accessibilityRole="header" style={s.heading}>Let’s connect.</Text></View>
        <View style={s.accessStage}><View style={[s.accessTile, jelly('ruby')]}>
          <NeonWell size={162} lit><View style={s.accessDisc}><BluetoothMark /></View></NeonWell>
          <Text style={s.accessLabel}>Bluetooth</Text><Rim radius={40} />
        </View></View>
        <View style={s.steps}>
          <Step number="01" title="Bluetooth on">Enable Bluetooth in Android quick settings.</Step>
          <Step number="02" title={'Allow ' + permission}>
            {modern ? 'Choose Allow when Android asks for Nearby devices access.'
              : 'Allow Location and turn Android Location on. Lighttime doesn’t collect your location.'}
          </Step>
        </View>
        <ObsidianButton label="Allow access & discover" disabled={light.working} onPress={onSearch} />
        <TextAction label="Open Bluetooth settings" onPress={onBluetoothSettings} />
      </>}

      {discovery && <>
        <View style={s.titleBlock}><Text accessibilityRole="header" style={s.heading}>{discoveryTitle}</Text></View>
        {!hasResults && <ProductHalo scanning={light.scanning || connecting} />}
        <View style={[s.scanStatus, glass]} accessibilityLiveRegion="polite">
          {light.scanning || light.working ? <ActivityIndicator size="small" color="#FFFFFF" /> : <View style={[s.dot, (off || denied || blocked || error) && s.warningDot]} />}
          <Text style={s.scanLabel}>{scanLabel}</Text>
        </View>
        {connecting && <View style={[s.connectingCard, glass]}>
          <Text style={s.stepTitle}>{deviceLabel(light.selected?.name)}</Text><Text style={s.small}>{light.selected?.name}</Text>
        </View>}
        {hasResults && <View style={s.results}>
          {light.devices.map(device => <Pressable key={device.id} accessibilityRole="button"
            accessibilityLabel={'Connect to ' + deviceLabel(device.name) + ', Bluetooth name ' + device.name + ', ' + signalLabel(device.rssi)}
            accessibilityState={{ disabled: light.working }} disabled={light.working} onPress={() => onConnect(device)}
            style={({ pressed }) => [s.device, jelly('ruby'), pressed && s.pressed]}>
            <View style={s.deviceTop}><View style={s.deviceCopy}><Text style={s.deviceTitle}>MOLUS G60</Text><Text selectable style={s.deviceIdentifier}>{device.name}</Text></View>
              <View style={s.connectCircle}><View accessible={false} style={s.forwardChevron} /></View></View>
            <View style={s.deviceBottom}><Signal rssi={device.rssi} /><Text style={s.small}>{signalLabel(device.rssi)}{device.rssi !== null ? ' · ' + device.rssi + ' dBm' : ''}</Text></View>
            <Text style={s.choose}>Connect</Text><Rim radius={32} />
          </Pressable>)}
        </View>}
        {recovery && !light.working && <Text selectable accessibilityLiveRegion="polite" style={off || denied || blocked || error ? s.error : s.body}>{recovery}</Text>}
        {light.scanning && <Text style={s.caption}>Keep your G60 on and nearby.</Text>}
        {connecting || light.scanning || light.working ? <ObsidianButton label={connecting ? 'Cancel connection' : 'Cancel search'} onPress={onCancel} />
          : light.unsupported ? <ObsidianButton label="Back to setup" onPress={() => onNavigate('prepare')} />
            : <View style={s.discoveryActions}>
              {blocked && <ObsidianButton label="Open app settings" onPress={onAppSettings} />}
              {off && <ObsidianButton label="Open Bluetooth settings" onPress={onBluetoothSettings} />}
              <Button secondary={blocked || off} label={denied && !blocked ? 'Allow access & try again' : light.hasScanned || blocked || off ? 'Search again' : 'Find my G60'} onPress={onSearch} />
            </View>}
      </>}

      {route === 'ready' && <>
        <View style={s.titleBlock}><Text accessibilityRole="header" style={s.heading}>Made for your moment.</Text></View>
        <ProductHalo scanning />
        <View style={[s.readyReadback, jelly('ruby')]} accessible accessibilityLabel={'Verified output: ' + (light.state?.brightness ?? 'unknown') + ' percent, ' + (light.state?.cct ?? 'unknown') + ' kelvin'}>
          <View style={s.readyName}><View style={s.dot} /><Text style={s.stepTitle}>{deviceLabel(light.selected?.name)}</Text></View>
          <View style={s.readbackValues}>
            <View style={s.readbackStat}><Text allowFontScaling={false} style={s.value}>{light.state?.brightness ?? '—'}<Text style={s.unit}>%</Text></Text><Text style={s.small}>Brightness</Text></View>
            <View style={s.readbackStat}><Text allowFontScaling={false} style={s.value}>{light.state?.cct ?? '—'}<Text style={s.unit}> K</Text></Text><Text style={s.small}>White balance</Text></View>
          </View><Rim radius={32} />
        </View>
        <View style={s.readyAction}><ObsidianButton label="Make it yours" onPress={() => onNavigate('main')} /></View>
      </>}

      {route === 'help' && <>
        <View style={s.titleBlock}><Text accessibilityRole="header" style={s.heading}>A clear connection.</Text></View>
        <View style={s.steps}>
          <Step number="01" title="Power & distance">Switch your G60 on and keep your phone within a few metres.</Step>
          <Step number="02" title="One controller">Disconnect ZY Vega or another control app. If needed, power the G60 off and on.</Step>
          <Step number="03" title="Bluetooth">Enable Bluetooth. Connect inside Lighttime; Android pairing and a code aren’t needed.</Step>
          <TextAction label="Open Bluetooth settings" onPress={onBluetoothSettings} />
          <Step number="04" title="Permission">{'Allow ' + permission + ' in Lighttime’s app settings.' + (modern ? '' : ' Turn Android Location on for discovery.')}</Step>
          <TextAction label="Open app settings" onPress={onAppSettings} />
          <Step number="05" title="Choose your light">G60 Bluetooth names begin with PL103. Compare names and move closer if several appear.</Step>
          <Step number="06" title="Verify output">After connecting, wait for actual brightness and white balance. Retry if readback fails.</Step>
        </View>
        <ObsidianButton label="Back to my setup" onPress={onBack} />
      </>}
      {actionError && !discovery && <Text accessibilityLiveRegion="polite" style={s.error}>{actionError}</Text>}
    </ScrollView>
  </View>;
}

const s = StyleSheet.create({
  shell: { flex: 1 },
  page: { paddingHorizontal: 22, paddingTop: 8, paddingBottom: 32, gap: 24, width: '100%', maxWidth: 600, alignSelf: 'center' },
  readyPage: { flexGrow: 1 },
  readyAction: { marginTop: 'auto', paddingTop: 20 },
  welcomePage: { flexGrow: 1, paddingHorizontal: 0, paddingBottom: 0, gap: 0 },
  topbar: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 12 },
  welcomeTopbar: { paddingHorizontal: 22 }, barTitle: { flex: 1, color: '#FFFFFF', fontFamily: face.medium, fontSize: 17, lineHeight: 25, textAlign: 'center' },
  barSpacer: { width: 48, height: 48 }, helpGlyph: { color: '#FFFFFF', fontFamily: face.medium, fontSize: 22 },
  backChevron: { width: 11, height: 11, borderLeftWidth: 2, borderBottomWidth: 2, borderColor: '#FFFFFF', transform: [{ rotate: '45deg' }], marginLeft: 4 },
  forwardChevron: { width: 11, height: 11, borderTopWidth: 2, borderRightWidth: 2, borderColor: '#FFFFFF', transform: [{ rotate: '45deg' }], marginRight: 3 },
  brandOrb: { width: 48, height: 48, borderRadius: 24, alignItems: 'center', justifyContent: 'center', backgroundColor: '#C22422',
    backgroundImage: 'radial-gradient(circle at 32% 24%, #FFD9D4 0%, #EF5B55 14%, #D3191C 42%, #340303 80%, #F68680 100%)',
    boxShadow: 'inset 0 1px 2px rgba(255,255,255,0.8), inset 0 -3px 6px rgba(255,156,156,0.5), 0 3px 20px rgba(255,25,25,0.5)' },
  brandCore: { width: 13, height: 13, borderRadius: 7, backgroundColor: '#160303', boxShadow: 'inset 0 1px 2px rgba(0,0,0,0.9), 0 1px 1px rgba(255,220,220,0.6)' },
  progress: { flexDirection: 'row', alignSelf: 'center', gap: 7, paddingVertical: 2 }, progressPart: { width: 24, height: 4, borderRadius: 2, backgroundColor: 'rgba(255,210,210,0.25)' },
  progressActive: { backgroundColor: '#FFD4D1', boxShadow: '0 0 9px rgba(255,175,175,0.55)' },
  titleBlock: { paddingTop: 4 }, heading: { color: '#FFFFFF', fontFamily: face.display, fontVariant: ['lining-nums'], fontSize: 32, lineHeight: 40, textAlign: 'center' },
  pink: { color: '#FFBBBC' }, body: { color: '#F2D2D1', fontFamily: face.regular, fontSize: 14, lineHeight: 22 },
  small: { color: '#F2D2D1', fontFamily: face.regular, fontSize: 13, lineHeight: 20, flexShrink: 1 },
  caption: { color: '#F2D2D1', fontFamily: face.regular, fontSize: 13, lineHeight: 21, textAlign: 'center' },
  welcomeHero: { height: 350, flexGrow: 1, alignItems: 'center', justifyContent: 'center' },
  heroGlow: { position: 'absolute', width: '130%', height: '110%', top: -12, backgroundImage: 'radial-gradient(ellipse closest-side at 50% 54%, rgba(255,59,39,0.9) 0%, rgba(255,59,39,0.78) 20%, rgba(255,59,39,0.51) 40%, rgba(255,59,39,0.23) 60%, rgba(255,59,39,0.05) 80%, rgba(255,59,39,0) 100%)' },
  welcomeProduct: { width: '143%', maxWidth: 750, marginTop: -12 },
  productName: { position: 'absolute', bottom: 16, color: '#FFFFFF', fontFamily: face.medium, fontSize: 15 },
  welcomeSheet: { borderTopLeftRadius: 42, borderTopRightRadius: 42, paddingHorizontal: 24, paddingTop: 26, paddingBottom: 16, gap: 18, overflow: 'hidden' },
  welcomeHeading: { color: '#FFFFFF', fontFamily: face.display, fontSize: 33, lineHeight: 42, textAlign: 'center', marginBottom: 5 },
  steps: { gap: 12 }, step: { padding: 17, borderRadius: 26, flexDirection: 'row', alignItems: 'flex-start', gap: 14 },
  stepDisc: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(255,180,180,0.15)', boxShadow: 'inset 0 1px 1px rgba(255,220,220,0.4)' },
  stepNumber: { color: '#FFFFFF', fontFamily: face.medium, fontSize: 14 }, stepCopy: { flex: 1, gap: 5 },
  stepTitle: { color: '#FFFFFF', fontFamily: face.medium, fontSize: 16, lineHeight: 23, flexShrink: 1 },
  textAction: { minHeight: 48, paddingVertical: 12, paddingHorizontal: 6, alignItems: 'center', justifyContent: 'center', borderRadius: 24 },
  textActionLabel: { color: '#FFFFFF', fontFamily: face.regular, fontSize: 14, lineHeight: 22, textAlign: 'center' },
  haloStage: { height: 260, width: '100%', alignItems: 'center', justifyContent: 'center' },
  haloCircle: { position: 'absolute', opacity: 0.85, transform: [{ rotateX: '15deg' }] },
  haloProduct: { position: 'absolute', width: '132%' },
  accessStage: { paddingVertical: 10, alignItems: 'center' },
  accessTile: { width: '80%', maxWidth: 320, borderRadius: 40, paddingVertical: 26, gap: 17, alignItems: 'center', transform: [{ rotate: '-4deg' }] },
  accessDisc: { width: 90, height: 90, borderRadius: 45, backgroundColor: '#F2D2D1', alignItems: 'center', justifyContent: 'center', boxShadow: '0 0 24px rgba(255,80,80,0.6), inset 0 -3px 7px rgba(130,0,0,0.25)' },
  accessLabel: { color: '#FFFFFF', fontFamily: face.medium, fontSize: 16 },
  bluetoothMark: { width: 34, height: 42 }, bluetoothLine: { position: 'absolute', height: 2.5, backgroundColor: '#210303', borderRadius: 2 },
  bluetoothStem: { left: 15, top: 1, width: 2.5, height: 40 }, bluetoothTop: { left: 13, top: 7, width: 18, transform: [{ rotate: '45deg' }] },
  bluetoothBottom: { left: 13, top: 33, width: 18, transform: [{ rotate: '-45deg' }] },
  bluetoothCrossUp: { left: 2, top: 26, width: 31, transform: [{ rotate: '-42deg' }] }, bluetoothCrossDown: { left: 2, top: 14, width: 31, transform: [{ rotate: '42deg' }] },
  scanStatus: { alignSelf: 'center', minHeight: 36, borderRadius: 18, paddingVertical: 7, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 10 },
  scanLabel: { color: '#FFFFFF', fontFamily: face.medium, fontSize: 14, lineHeight: 22, flexShrink: 1 },
  dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: '#FFDAD5', boxShadow: '0 0 9px rgba(255,140,120,0.9)' }, warningDot: { backgroundColor: '#FFB59E' },
  results: { gap: 16 }, device: { borderRadius: 32, padding: 23, gap: 18, overflow: 'hidden' },
  deviceTop: { flexDirection: 'row', alignItems: 'center', gap: 12 }, deviceCopy: { flex: 1, gap: 5 },
  deviceTitle: { color: '#FFFFFF', fontFamily: face.medium, fontSize: 20, lineHeight: 28 },
  deviceIdentifier: { color: '#F2D2D1', fontFamily: face.regular, fontSize: 14, lineHeight: 22 },
  connectCircle: { width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center', backgroundColor: 'rgba(255,225,225,0.11)', boxShadow: 'inset 0 1px 1px rgba(255,235,235,0.5)' },
  deviceBottom: { flexDirection: 'row', alignItems: 'center', gap: 10 }, signal: { height: 22, flexDirection: 'row', alignItems: 'flex-end', gap: 3 },
  signalBar: { width: 4, borderRadius: 2, backgroundColor: '#FFD4D1' }, choose: { color: '#FFFFFF', fontFamily: face.medium, fontSize: 15, lineHeight: 23, textAlign: 'right' },
  connectingCard: { borderRadius: 26, padding: 20, gap: 5 }, discoveryActions: { gap: 12 },
  readyReadback: { borderRadius: 32, padding: 24, gap: 20, overflow: 'hidden' }, readyName: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8 },
  readbackValues: { flexDirection: 'row', flexWrap: 'wrap', gap: 18, justifyContent: 'space-around' }, readbackStat: { alignItems: 'center', gap: 5 },
  value: { color: '#FFFFFF', fontFamily: face.semibold, fontSize: 36, lineHeight: 44, fontVariant: ['tabular-nums'] }, unit: { fontSize: 16, fontFamily: face.medium },
  error: { color: '#FFD0BE', fontFamily: face.regular, fontSize: 14, lineHeight: 23, textAlign: 'center' }, pressed: { opacity: 0.78 },
});
