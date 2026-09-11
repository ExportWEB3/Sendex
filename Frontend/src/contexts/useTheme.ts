import { createContext, useContext } from 'react';
import type { ThemeContextType } from '../../typefiles';

export const ThemeContext = createContext<ThemeContextType>({ isDark: false, toggle: () => {} });

export function useTheme() {
  return useContext(ThemeContext);
}