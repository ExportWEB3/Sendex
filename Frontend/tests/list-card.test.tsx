// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ListCard } from '../src/components/ListCard';
import type { RecipientList } from '../typefiles';

const longName = 'Reuters_Thomson_Reuters_Financial_Conditions_Review_Batch_14_2026_09_11';
const list: RecipientList = {
  id: 14,
  name: longName,
  description: 'A long imported list description that must remain inside its card.',
  recipient_count: 20,
  active_count: 20,
  unsubscribed_count: 0,
  bounced_count: 0,
  is_active: true,
  created_at: '2026-09-13T00:00:00Z',
};

describe('ListCard', () => {
  afterEach(cleanup);

  it('constrains and wraps long imported names inside the card', () => {
    const { container } = render(
      <ListCard
        list={list}
        onViewEmails={vi.fn()}
        onAddRecipients={vi.fn()}
        onDelete={vi.fn()}
      />,
    );

    const heading = screen.getByRole('heading', { name: longName });
    expect(heading.className).toContain('line-clamp-2');
    expect(heading.className).toContain('wrap-anywhere');
    expect(heading.getAttribute('title')).toBe(longName);
    expect(container.firstElementChild?.className).toContain('overflow-hidden');
  });
});
