import React from 'react';

export interface IconButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  /** Required accessible name for assistive tech — the visible `title` alone is not
   * announced as a label by all screen readers. */
  'aria-label': string;
}

/** A minimal, accessible icon-only button. Wraps the app's base `button` styling
 * (see styles.css) with an `icon-button` class that guarantees a 32x32px minimum
 * clickable box, and enforces `aria-label` at the type level so icon-only actions
 * are never left unlabeled for screen readers. Pass `title` too, for mouse users'
 * tooltips. */
export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ className, children, ...props }, ref) => (
    <button
      ref={ref}
      className={className ? `icon-button ${className}` : 'icon-button'}
      {...props}
    >
      {children}
    </button>
  )
);

IconButton.displayName = 'IconButton';
