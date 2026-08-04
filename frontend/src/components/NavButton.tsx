import React from 'react';
import { NavItem } from './NavItem';

export function NavButton(props: { icon: React.ReactNode; label: string; active: boolean; onClick: () => void; tourId?: string }) {
  return <NavItem {...props} />;
}
