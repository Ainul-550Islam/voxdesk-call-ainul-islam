
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { UseCaseSearchInput } from '../components/use-cases/UseCaseSearchInput';
import { UseCaseCard } from '../components/use-cases/UseCaseCard';
import { UseCaseFilterBar } from '../components/use-cases/UseCaseFilterBar';
import type { UseCaseSummary, UseCaseCategory } from '../types/use-case';

describe('Use Cases Page', () => {
  const mockUseCase: UseCaseSummary = {
    slug: 'ai-receptionist',
    title: 'AI Receptionist',
    category: 'receptionists',
    category_title: 'Receptionists & Answering',
    description: 'AI receptionist handling inbound calls',
    capabilities: ['knowledge-base', 'tools'],
    supported: true,
    featured: true,
  };

  const mockCategories: UseCaseCategory[] = [
    { id: 'all', title: 'All Use Cases', slug: 'all' },
    { id: 'receptionists', title: 'Receptionists & Answering', slug: 'receptionists' },
  ];

  it('renders search input', () => {
    render(<UseCaseSearchInput value="" onChange={vi.fn()} />);
    expect(screen.getByLabelText(/search use cases/i)).toBeInTheDocument();
  });

  it('renders use case card', () => {
    render(<UseCaseCard useCase={mockUseCase} />);
    expect(screen.getByText('AI Receptionist')).toBeInTheDocument();
  });

  it('renders filter bar', () => {
    render(<UseCaseFilterBar categories={mockCategories} activeCategory="all" total={1} onCategoryChange={vi.fn()} />);
    expect(screen.getByText('All Use Cases')).toBeInTheDocument();
  });

  it('handles search clear', () => {
    const onClear = vi.fn();
    render(<UseCaseSearchInput value="test" onChange={vi.fn()} onClear={onClear} />);
    const clearBtn = screen.getByLabelText(/clear search/i);
    fireEvent.click(clearBtn);
    expect(onClear).toHaveBeenCalled();
  });

  it('handles card click', () => {
    const onClick = vi.fn();
    render(<UseCaseCard useCase={mockUseCase} onClick={onClick} />);
    const card = screen.getByRole('button');
    fireEvent.click(card);
    expect(onClick).toHaveBeenCalledWith('ai-receptionist');
  });

  it('shows featured badge', () => {
    render(<UseCaseCard useCase={mockUseCase} featured />);
    expect(screen.getByText('Featured')).toBeInTheDocument();
  });

  it('shows capabilities', () => {
    render(<UseCaseCard useCase={mockUseCase} />);
    expect(screen.getByText('knowledge-base')).toBeInTheDocument();
  });

  it('handles keyboard navigation', () => {
    const onClick = vi.fn();
    render(<UseCaseCard useCase={mockUseCase} onClick={onClick} />);
    const card = screen.getByRole('button');
    fireEvent.keyDown(card, { key: 'Enter' });
    expect(onClick).toHaveBeenCalled();
  });

  it('shows category badge', () => {
    render(<UseCaseCard useCase={mockUseCase} />);
    expect(screen.getByText(/receptionists/i)).toBeInTheDocument();
  });

  it('handles empty categories', () => {
    render(<UseCaseFilterBar categories={[]} activeCategory="all" total={0} onCategoryChange={vi.fn()} />);
    expect(screen.getByText('All')).toBeInTheDocument();
  });
});
