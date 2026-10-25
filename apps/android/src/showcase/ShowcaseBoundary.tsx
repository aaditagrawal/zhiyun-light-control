import { Component } from 'react';
import type { ReactNode } from 'react';
import { Modal, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { ObsidianButton } from '../pattern-ui/materials';
import { G60Artwork } from '../ui/G60Artwork';
import { color, font } from '../ui/theme';

/** A viewer/module failure must never unmount the light's connection owner. */
export class ShowcaseBoundary extends Component<{ onClose: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return <Modal visible statusBarTranslucent navigationBarTranslucent onRequestClose={this.props.onClose}>
      <SafeAreaView style={s.screen}>
        <View style={s.product}><G60Artwork /></View>
        <Text accessibilityLiveRegion="polite" style={s.message}>The 3D preview is unavailable.</Text>
        <ObsidianButton label="Close preview" onPress={this.props.onClose} />
      </SafeAreaView>
    </Modal>;
  }
}
const s = StyleSheet.create({
  screen: { flex: 1, justifyContent: 'center', padding: 24, gap: 24, backgroundColor: '#150505' },
  product: { width: '100%', flexShrink: 1 },
  message: { color: color.text, fontFamily: font.regular, fontSize: 16, lineHeight: 24, textAlign: 'center' },
});
