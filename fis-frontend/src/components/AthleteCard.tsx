/**
 * AthleteCard - Card component for athlete grid
 */

import { useNavigate } from 'react-router-dom';
import type { Athlete } from '../types';

interface AthleteCardProps {
  athlete: Athlete;
}

export default function AthleteCard({ athlete }: AthleteCardProps) {
  const navigate = useNavigate();

  const getTierColor = (tier?: string) => {
    switch (tier) {
      case 'Elite':
        return 'badge-primary';
      case 'Contender':
        return 'badge-positive';
      default:
        return 'badge bg-gray-700 text-gray-300';
    }
  };

  return (
    <div
      onClick={() => navigate(`/athletes/${athlete.fis_code}`)}
      className="card-hover cursor-pointer p-5"
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-bold text-gray-100 truncate">
            {athlete.name}
          </h3>
          <p className="text-sm text-gray-500">{athlete.country || 'Unknown'}</p>
        </div>
        {athlete.tier && (
          <span className={getTierColor(athlete.tier)}>
            {athlete.tier}
          </span>
        )}
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="text-center">
          <div className="text-2xl font-bold text-primary-400">
            {athlete.wins || 0}
          </div>
          <div className="text-xs text-gray-500">Wins</div>
        </div>
        <div className="text-center">
          <div className="text-2xl font-bold text-gray-300">
            {athlete.podiums || 0}
          </div>
          <div className="text-xs text-gray-500">Podiums</div>
        </div>
        <div className="text-center">
          <div className="text-2xl font-bold text-gray-300">
            {athlete.starts || 0}
          </div>
          <div className="text-xs text-gray-500">Starts</div>
        </div>
      </div>

      {/* FIS Points */}
      {athlete.avg_fis_points !== undefined && (
        <div className="pt-3 border-t border-gray-800">
          <div className="flex justify-between items-center">
            <span className="text-xs text-gray-500">Avg FIS Points</span>
            <span className="text-sm font-semibold text-gray-300">
              {athlete.avg_fis_points.toFixed(2)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
