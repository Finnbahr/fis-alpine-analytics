/**
 * Dashboard - Grid of tracked athletes
 */

import { useState, useEffect } from 'react';
import { getAthletes } from '../services/api';
import AthleteCard from '../components/AthleteCard';
import LoadingSpinner from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import type { Athlete } from '../types';

export default function Dashboard() {
  const [athletes, setAthletes] = useState<Athlete[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [discipline, setDiscipline] = useState<string>('');
  const [tier, setTier] = useState<string>('');

  useEffect(() => {
    fetchAthletes();
  }, [discipline, tier]);

  const fetchAthletes = async () => {
    try {
      setLoading(true);
      setError(null);
      const params: any = { limit: 50 };
      if (discipline) params.discipline = discipline;
      if (tier) params.tier = tier;

      const response = await getAthletes(params);
      setAthletes(response.data || []);
    } catch (err) {
      console.error('Error fetching athletes:', err);
      setError('Failed to load athletes. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  const disciplines = ['Slalom', 'Giant Slalom', 'Super G', 'Downhill', 'Alpine Combined'];
  const tiers = ['Elite', 'Contender', 'Middle', 'Developing'];

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-100 mb-2">Your Athletes</h1>
        <p className="text-gray-400">Track and analyze elite ski racers</p>
      </div>

      {/* Filters */}
      <div className="mb-6 flex flex-wrap gap-4">
        <div>
          <label className="block text-sm font-medium text-gray-400 mb-2">
            Discipline
          </label>
          <select
            value={discipline}
            onChange={(e) => setDiscipline(e.target.value)}
            className="input w-48"
          >
            <option value="">All Disciplines</option>
            {disciplines.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-400 mb-2">
            Tier
          </label>
          <select
            value={tier}
            onChange={(e) => setTier(e.target.value)}
            className="input w-48"
          >
            <option value="">All Tiers</option>
            {tiers.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        {(discipline || tier) && (
          <div className="flex items-end">
            <button
              onClick={() => {
                setDiscipline('');
                setTier('');
              }}
              className="btn-secondary text-sm"
            >
              Clear Filters
            </button>
          </div>
        )}
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex justify-center py-16">
          <LoadingSpinner />
        </div>
      ) : error ? (
        <ErrorMessage message={error} />
      ) : athletes.length === 0 ? (
        <div className="card text-center py-12">
          <p className="text-gray-400">No athletes found with the selected filters.</p>
          <button
            onClick={() => {
              setDiscipline('');
              setTier('');
            }}
            className="btn-primary mt-4"
          >
            Clear Filters
          </button>
        </div>
      ) : (
        <>
          <div className="mb-4 text-sm text-gray-500">
            Showing {athletes.length} athletes
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {athletes.map((athlete) => (
              <AthleteCard key={athlete.fis_code} athlete={athlete} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
