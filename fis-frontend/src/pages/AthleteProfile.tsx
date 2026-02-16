import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  getAthlete,
  getAthleteRaces,
  getAthleteMomentum,
  getAthleteCourses,
} from '../services/api';
import type { AthleteProfile as AthleteProfileType } from '../types';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { TrophyIcon, FireIcon, MapPinIcon, ChartBarIcon } from '@heroicons/react/24/solid';
import { PageLoader } from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';

export default function AthleteProfile() {
  const { fisCode } = useParams<{ fisCode: string }>();
  const [profile, setProfile] = useState<AthleteProfileType | null>(null);
  const [races, setRaces] = useState<any[]>([]);
  const [momentum, setMomentum] = useState<any[]>([]);
  const [courses, setCourses] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'races' | 'momentum' | 'courses'>('races');

  useEffect(() => {
    const fetchData = async () => {
      if (!fisCode) return;

      setLoading(true);
      setError(null);
      try {
        // Fetch profile first (required)
        const profileData = await getAthlete(fisCode);
        setProfile(profileData);

        // Fetch optional data (don't fail if missing)
        try {
          const racesData = await getAthleteRaces(fisCode, { limit: 20 });
          setRaces(racesData.data || []);
        } catch (err) {
          console.warn('No race data available');
          setRaces([]);
        }

        try {
          const momentumData = await getAthleteMomentum(fisCode, { limit: 50 });
          setMomentum(momentumData.data || []);
        } catch (err) {
          console.warn('No momentum data available');
          setMomentum([]);
        }

        try {
          const coursesData = await getAthleteCourses(fisCode, { min_races: 3 });
          setCourses(coursesData.data || []);
        } catch (err) {
          console.warn('No course performance data available');
          setCourses([]);
        }
      } catch (error) {
        console.error('Failed to fetch athlete profile:', error);
        setError('Failed to load athlete profile. The athlete may not exist or there was a server error.');
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [fisCode]);

  if (loading) {
    return <PageLoader />;
  }

  if (error || !profile) {
    return (
      <ErrorMessage
        title="Athlete Not Found"
        message={error || "The requested athlete profile could not be found."}
      />
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="card mb-8">
        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className="text-4xl font-bold text-gray-100 mb-2">{profile.name}</h1>
            <div className="flex items-center space-x-4 text-gray-400">
              {profile.country && (
                <span className="flex items-center">
                  <span className="text-2xl mr-2">🇺🇸</span>
                  {profile.country}
                </span>
              )}
              <span className="text-sm">FIS Code: {profile.fis_code}</span>
            </div>
          </div>
          {profile.momentum && (
            <div className="text-right">
              <div className="text-sm text-gray-500 mb-1">Current Momentum</div>
              <div className="flex items-center space-x-2">
                <FireIcon className={`h-6 w-6 ${
                  profile.momentum.trend === 'hot' ? 'text-emerald-400' :
                  profile.momentum.trend === 'cold' ? 'text-primary-400' : 'text-gray-500'
                }`} />
                <span className="text-2xl font-bold text-gray-100">
                  {profile.momentum.current_momentum_z?.toFixed(2) || 'N/A'}
                </span>
              </div>
              <div className="text-xs text-gray-500 capitalize">{profile.momentum.trend}</div>
            </div>
          )}
        </div>

        {/* Career Stats */}
        {profile.career_stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6 pt-6 border-t border-gray-800">
            <StatBox
              icon={<ChartBarIcon className="h-8 w-8 text-primary-400" />}
              label="Total Starts"
              value={profile.career_stats.starts}
            />
            <StatBox
              icon={<TrophyIcon className="h-8 w-8 text-yellow-400" />}
              label="Wins"
              value={profile.career_stats.wins}
            />
            <StatBox
              icon={<TrophyIcon className="h-8 w-8 text-gray-400" />}
              label="Podiums"
              value={profile.career_stats.podiums}
            />
            <StatBox
              icon={<ChartBarIcon className="h-8 w-8 text-primary-400" />}
              label="Avg FIS Points"
              value={profile.career_stats.avg_fis_points.toFixed(1)}
            />
          </div>
        )}

        {/* Current Tier */}
        {profile.current_tier && (
          <div className="mt-6 pt-6 border-t border-gray-800">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-sm text-gray-400">Current Tier ({profile.current_tier.year}):</span>
                <span className="ml-2 font-bold text-primary-400">{profile.current_tier.tier}</span>
                <span className="ml-2 text-gray-400">in {profile.current_tier.discipline}</span>
              </div>
              <div className="text-right">
                <div className="text-sm text-gray-400">Season Stats</div>
                <div className="text-lg font-semibold text-gray-100">
                  {profile.current_tier.avg_fis_points.toFixed(1)} pts
                  <span className="text-sm text-gray-400 ml-2">
                    ({profile.current_tier.race_count} races)
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="mb-6 border-b border-gray-800">
        <div className="flex space-x-8">
          <TabButton
            active={activeTab === 'races'}
            onClick={() => setActiveTab('races')}
            icon={<ChartBarIcon className="h-5 w-5" />}
            label="Race History"
          />
          <TabButton
            active={activeTab === 'momentum'}
            onClick={() => setActiveTab('momentum')}
            icon={<FireIcon className="h-5 w-5" />}
            label="Momentum"
          />
          <TabButton
            active={activeTab === 'courses'}
            onClick={() => setActiveTab('courses')}
            icon={<MapPinIcon className="h-5 w-5" />}
            label="Course Performance"
          />
        </div>
      </div>

      {/* Tab Content */}
      {activeTab === 'races' && (
        <div className="card">
          <h2 className="text-2xl font-bold text-gray-100 mb-6">Recent Race Results</h2>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-800">
                  <th className="text-left py-3 px-4 font-semibold text-gray-100">Date</th>
                  <th className="text-left py-3 px-4 font-semibold text-gray-100">Location</th>
                  <th className="text-left py-3 px-4 font-semibold text-gray-100">Discipline</th>
                  <th className="text-right py-3 px-4 font-semibold text-gray-100">Rank</th>
                  <th className="text-right py-3 px-4 font-semibold text-gray-100">FIS Points</th>
                  <th className="text-right py-3 px-4 font-semibold text-gray-100">Z-Score</th>
                </tr>
              </thead>
              <tbody>
                {races.map((race, idx) => (
                  <tr key={idx} className="border-b border-gray-800 hover:bg-gray-800/50">
                    <td className="py-3 px-4 text-gray-400">
                      {new Date(race.date).toLocaleDateString()}
                    </td>
                    <td className="py-3 px-4">
                      <div className="font-medium text-gray-100">{race.location}</div>
                      <div className="text-sm text-gray-500">{race.country}</div>
                    </td>
                    <td className="py-3 px-4 text-gray-400">{race.discipline}</td>
                    <td className="py-3 px-4 text-right font-semibold text-gray-100">
                      {race.rank}
                    </td>
                    <td className="py-3 px-4 text-right text-gray-300">
                      {race.fis_points?.toFixed(1) || 'N/A'}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <span className={`font-semibold ${
                        race.race_z_score > 0 ? 'text-emerald-400' : 'text-red-400'
                      }`}>
                        {race.race_z_score?.toFixed(2) || 'N/A'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === 'momentum' && (
        <div className="space-y-6">
          <div className="card">
            <h2 className="text-2xl font-bold text-gray-100 mb-6">Momentum Over Time</h2>
            <ResponsiveContainer width="100%" height={400}>
              <LineChart data={momentum}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis
                  dataKey="date"
                  tickFormatter={(date) => new Date(date).toLocaleDateString('en-US', { month: 'short', year: '2-digit' })}
                  stroke="#9ca3af"
                />
                <YAxis stroke="#9ca3af" />
                <Tooltip
                  labelFormatter={(date) => new Date(date).toLocaleDateString()}
                  formatter={(value) => (value as number).toFixed(2)}
                  contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '0.5rem' }}
                  labelStyle={{ color: '#f3f4f6' }}
                />
                <Legend wrapperStyle={{ color: '#f3f4f6' }} />
                <Line
                  type="monotone"
                  dataKey="momentum_z"
                  stroke="#f97316"
                  strokeWidth={2}
                  name="Momentum Z-Score"
                  dot={{ fill: '#f97316' }}
                />
                <Line
                  type="monotone"
                  dataKey="race_z_score"
                  stroke="#0ea5e9"
                  strokeWidth={2}
                  name="Race Z-Score"
                  dot={{ fill: '#0ea5e9' }}
                />
              </LineChart>
            </ResponsiveContainer>
            <div className="mt-4 text-sm text-gray-400">
              <p>
                <strong className="text-gray-300">Momentum Z-Score:</strong> Higher values indicate better recent form (hot streak)
              </p>
              <p className="mt-1">
                <strong className="text-gray-300">Race Z-Score:</strong> Performance relative to field (positive = above average)
              </p>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'courses' && (
        <div className="card">
          <h2 className="text-2xl font-bold text-gray-100 mb-6">Performance by Course</h2>
          <ResponsiveContainer width="100%" height={400}>
            <BarChart data={courses.slice(0, 15)}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis
                dataKey="location"
                angle={-45}
                textAnchor="end"
                height={120}
                tick={{ fontSize: 12, fill: '#9ca3af' }}
              />
              <YAxis stroke="#9ca3af" />
              <Tooltip
                formatter={(value) => (value as number).toFixed(2)}
                contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151', borderRadius: '0.5rem' }}
                labelStyle={{ color: '#f3f4f6' }}
              />
              <Legend wrapperStyle={{ color: '#f3f4f6' }} />
              <Bar dataKey="mean_race_z_score" fill="#0ea5e9" name="Avg Z-Score" />
            </BarChart>
          </ResponsiveContainer>
          <div className="mt-6">
            <h3 className="font-semibold text-gray-100 mb-4">Top Courses</h3>
            <div className="grid md:grid-cols-2 gap-4">
              {courses.slice(0, 6).map((course, idx) => (
                <div key={idx} className="p-4 bg-gray-800 rounded-lg border border-gray-700">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="font-semibold text-gray-100">{course.location}</div>
                      <div className="text-sm text-gray-400">{course.discipline}</div>
                    </div>
                    <div className="text-right">
                      <div className="font-bold text-primary-400">
                        {course.mean_race_z_score.toFixed(2)}
                      </div>
                      <div className="text-xs text-gray-500">{course.race_count} races</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatBox({ icon, label, value }: { icon: React.ReactNode; label: string; value: string | number }) {
  return (
    <div className="text-center">
      <div className="flex justify-center mb-2">{icon}</div>
      <div className="text-3xl font-bold text-gray-100 mb-1">{value}</div>
      <div className="text-sm text-gray-400">{label}</div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center space-x-2 pb-4 border-b-2 transition-colors ${
        active
          ? 'border-primary-400 text-primary-400'
          : 'border-transparent text-gray-400 hover:text-gray-100'
      }`}
    >
      {icon}
      <span className="font-medium">{label}</span>
    </button>
  );
}
