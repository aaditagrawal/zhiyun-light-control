import React from 'react';
import { View } from 'react-native';
export const SafeAreaView = View;
export const SafeAreaProvider = ({ children }: any) => <View style={{ flex: 1 }}>{children}</View>;
export const useSafeAreaInsets = () => ({ top: 0, right: 0, bottom: 0, left: 0 });
export const StatusBar = () => null;
