// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PageSearch } from '../src/components/PageSearch';
import { matchesPageSearch } from '../src/utils/page-search';

describe('page search', () => {
  afterEach(cleanup);

  it('matches every search term across the displayed fields', () => {
    expect(matchesPageSearch('china active', [
      'China Partner Account',
      'sender@example.com',
      'active',
    ])).toBe(true);
    expect(matchesPageSearch('china paused', [
      'China Partner Account',
      'sender@example.com',
      'active',
    ])).toBe(false);
    expect(matchesPageSearch('   ', ['anything'])).toBe(true);
  });

  it('reports filtered counts and supports typing and clearing', () => {
    const onChange = vi.fn();
    render(
      <PageSearch
        value="china"
        onChange={onChange}
        placeholder="Search sending accounts..."
        label="Search sending accounts"
        resultCount={3}
        totalCount={12}
      />,
    );

    expect(screen.getByText('3 of 12 shown')).toBeTruthy();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'keller' } });
    expect(onChange).toHaveBeenCalledWith('keller');
    fireEvent.click(screen.getByRole('button', { name: 'Clear search sending accounts' }));
    expect(onChange).toHaveBeenCalledWith('');
  });
});
