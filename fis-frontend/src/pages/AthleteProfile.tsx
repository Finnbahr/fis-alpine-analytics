import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  getAthlete,
  getAthleteRaces,
  getAthleteMomentum,
  getAthleteCourses,
  getAthleteStrokesGained,
  getAthleteStrokesGainedBib,
  getAthleteRegression,
  getAthleteCourseTraits,
} from '../services/api';
import type { AthleteProfile as AthleteProfileType } from '../types';
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { TrophyIcon, FireIcon, MapPinIcon, ChartBarIcon, BeakerIcon, AcademicCapIcon } from '@heroicons/react/24/solid';
import { PageLoader } from '../components/LoadingSpinner';
import ErrorMessage from '../components/ErrorMessage';
import StrokesGainedChart from '../components/charts/StrokesGainedChart';
import RegressionChart from '../components/charts/RegressionChart';
import CourseTraitsChart from '../components/charts/CourseTraitsChart';

export default function AthleteProfile() {
  const { fisCode } = useParams<{ fisCode: string }>();
  const [profile, setProfile] = useState<AthleteProfileType | null>(null);
  const [races, setRaces] = useState<any[]>([]);
  const [momentum, setMomentum] = useState<any[]>([]);
  const [courses, setCourses] = useState<any[]>([]);
  const [strokesGained, setStrokesGained] = useState<any[]>([]);
  const [strokesGainedBib, setStrokesGainedBib] = useState<any[]>([]);
  const [regression, setRegression] = useState<any>(null);
  const [courseTraits, setCourseTraits] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'races' | 'momentum' | 'performance' | 'course-analysis' | 'courses'>('races');

  // Filters
  const [selectedYear, setSelectedYear] = useState<string>('');
  const [selectedDiscipline, setSelectedDiscipline] = useState<string>('');

  // Filter function
  const filterByYearAndDiscipline = (item: any) => {
    const date = new Date(item.date);
    const year = date.getFullYear().toString();
    return (
      (!selectedYear || year === selectedYear) &&
      (!selectedDiscipline || item.discipline === selectedDiscipline)
    );
  };

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

        try {
          const strokesGainedData = await getAthleteStrokesGained(fisCode, { limit: 50 });
          setStrokesGained(strokesGainedData.data || []);
        } catch (err) {
          console.warn('No strokes gained data available');
          setStrokesGained([]);
        }

        try {
          const strokesGainedBibData = await getAthleteStrokesGainedBib(fisCode, { limit: 50 });
          setStrokesGainedBib(strokesGainedBibData.data || []);
        } catch (err) {
          console.warn('No strokes gained bib data available');
          setStrokesGainedBib([]);
        }

        try {
          const regressionData = await getAthleteRegression(fisCode);
          setRegression(regressionData);
        } catch (err) {
          console.warn('No regression data available');
          setRegression(null);
        }

        try {
          const courseTraitsData = await getAthleteCourseTraits(fisCode);
          setCourseTraits(courseTraitsData);
        } catch (err) {
          console.warn('No course traits data available');
          setCourseTraits(null);
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

  // Re-fetch aggregate data when year or discipline filter changes
  useEffect(() => {
    const fetchAggregateData = async () => {
      if (!fisCode) return;

      const params: { discipline?: string; year?: number } = {};
      if (selectedDiscipline) params.discipline = selectedDiscipline;
      if (selectedYear) params.year = parseInt(selectedYear);

      try {
        const regressionData = await getAthleteRegression(fisCode, params);
        setRegression(regressionData);
      } catch (err) {
        console.warn('No regression data available for selected filters');
        setRegression(null);
      }

      try {
        const courseTraitsData = await getAthleteCourseTraits(fisCode, params);
        setCourseTraits(courseTraitsData);
      } catch (err) {
        console.warn('No course traits data available for selected filters');
        setCourseTraits(null);
      }
    };

    fetchAggregateData();
  }, [fisCode, selectedDiscipline, selectedYear]);

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
            <div>
              <span className="text-sm text-gray-400">Current Tier ({profile.current_tier.year}):</span>
              <span className="ml-2 font-bold text-primary-400">{profile.current_tier.tier}</span>
              <span className="ml-2 text-gray-400">in {profile.current_tier.discipline}</span>
            </div>
          </div>
        )}
      </div>

      {/* Filters */}
      <div className="mb-8 bg-gradient-to-br from-black/60 to-cyan-900/10 border border-cyan-500/40 rounded-xl p-6 backdrop-blur-md shadow-xl shadow-cyan-500/10">
        <div className="flex items-center gap-3 mb-4">
          <div className="h-8 w-1 bg-cyan-400 rounded-full"></div>
          <h3 className="text-sm font-bold text-cyan-400 uppercase tracking-wider">Filters</h3>
          {(selectedYear || selectedDiscipline) && (
            <span className="text-xs text-gray-400 bg-cyan-500/10 px-2 py-0.5 rounded-full border border-cyan-500/30">
              {[selectedYear, selectedDiscipline].filter(Boolean).length} active
            </span>
          )}
        </div>

        <div className="flex flex-wrap gap-4 items-end">
          <div className="flex-1 min-w-[140px]">
            <label className="block text-xs font-semibold text-cyan-300 mb-2 uppercase tracking-wider flex items-center gap-2">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              Year
            </label>
            <select
              value={selectedYear}
              onChange={(e) => setSelectedYear(e.target.value)}
              className="w-full px-4 py-2.5 bg-black/70 border border-cyan-500/50 text-gray-100 rounded-lg focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-transparent transition-all hover:border-cyan-400 text-sm font-medium shadow-inner"
            >
              <option value="">All Years</option>
              {Array.from(new Set(races.map(r => new Date(r.date).getFullYear())))
                .sort((a, b) => b - a)
                .map(year => (
                  <option key={year} value={year}>{year}</option>
                ))}
            </select>
          </div>

          <div className="flex-1 min-w-[180px]">
            <label className="block text-xs font-semibold text-cyan-300 mb-2 uppercase tracking-wider flex items-center gap-2">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
              </svg>
              Discipline
            </label>
            <select
              value={selectedDiscipline}
              onChange={(e) => setSelectedDiscipline(e.target.value)}
              className="w-full px-4 py-2.5 bg-black/70 border border-cyan-500/50 text-gray-100 rounded-lg focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:border-transparent transition-all hover:border-cyan-400 text-sm font-medium shadow-inner"
            >
              <option value="">All Disciplines</option>
              {Array.from(new Set(races.map(r => r.discipline)))
                .sort()
                .map(disc => (
                  <option key={disc} value={disc}>{disc}</option>
                ))}
            </select>
          </div>

          {(selectedYear || selectedDiscipline) && (
            <button
              onClick={() => {
                setSelectedYear('');
                setSelectedDiscipline('');
              }}
              className="px-5 py-2.5 bg-cyan-500/10 text-cyan-400 rounded-lg hover:bg-cyan-500/20 transition-all font-medium border border-cyan-500/40 hover:border-cyan-400 flex items-center gap-2 text-sm shadow-lg hover:shadow-cyan-500/20"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
              Clear
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="mb-6 border-b border-gray-800">
        <div className="flex space-x-6 overflow-x-auto">
          <TabButton
            active={activeTab === 'races'}
            onClick={() => setActiveTab('races')}
            icon={<ChartBarIcon className="h-5 w-5" />}
            label="Races"
          />
          <TabButton
            active={activeTab === 'momentum'}
            onClick={() => setActiveTab('momentum')}
            icon={<FireIcon className="h-5 w-5" />}
            label="Momentum"
          />
          <TabButton
            active={activeTab === 'performance'}
            onClick={() => setActiveTab('performance')}
            icon={<BeakerIcon className="h-5 w-5" />}
            label="Performance"
          />
          <TabButton
            active={activeTab === 'course-analysis'}
            onClick={() => setActiveTab('course-analysis')}
            icon={<AcademicCapIcon className="h-5 w-5" />}
            label="Course Analysis"
          />
          <TabButton
            active={activeTab === 'courses'}
            onClick={() => setActiveTab('courses')}
            icon={<MapPinIcon className="h-5 w-5" />}
            label="Top Courses"
          />
        </div>
      </div>

      {/* Tab Content */}
      {activeTab === 'races' && (() => {
        const filteredRaces = races.filter(filterByYearAndDiscipline);

        return (
          <div className="card">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-2xl font-bold text-gray-100">Race Results</h2>
              <div className="text-sm text-gray-400">
                Showing {filteredRaces.length} of {races.length} races
              </div>
            </div>
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
                  {filteredRaces.map((race, idx) => (
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
        );
      })()}

      {activeTab === 'momentum' && (
        <div className="space-y-6">
          <div className="card bg-black/40 border-cyan-500/20">
            <h2 className="text-2xl font-bold text-gray-100 mb-6">Momentum Over Time</h2>
            <ResponsiveContainer width="100%" height={400}>
              <LineChart data={momentum.filter(filterByYearAndDiscipline)}>
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

      {activeTab === 'performance' && (
        <div className="space-y-6">
          {strokesGained.length > 0 ? (
            <StrokesGainedChart
              strokesGainedData={strokesGained.filter(filterByYearAndDiscipline)}
              bibData={strokesGainedBib.filter(filterByYearAndDiscipline)}
            />
          ) : (
            <div className="card text-center py-12">
              <p className="text-gray-400">No performance data available for this athlete.</p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'course-analysis' && (
        <div className="space-y-6">
          {regression && regression.data && regression.data.length > 0 ? (
            <RegressionChart
              data={regression.data}
              discipline={regression.discipline || 'All'}
            />
          ) : (
            <div className="card text-center py-12">
              <p className="text-gray-400">No regression analysis available for this athlete.</p>
            </div>
          )}

          {courseTraits && courseTraits.data && courseTraits.data.length > 0 ? (
            <>
              {/* Group by trait */}
              {Array.from(new Set(courseTraits.data.map((d: any) => d.trait))).map((trait: any) => {
                const traitData = courseTraits.data.filter((d: any) => d.trait === trait);
                return traitData.length > 0 ? (
                  <CourseTraitsChart key={trait} data={traitData} trait={trait} />
                ) : null;
              })}
            </>
          ) : (
            <div className="card text-center py-12 mt-6">
              <p className="text-gray-400">No course trait analysis available for this athlete.</p>
            </div>
          )}
        </div>
      )}

      {activeTab === 'courses' && (() => {
        const filteredCourses = courses.filter(filterByYearAndDiscipline);
        return (
        <div className="card bg-black/40 border-cyan-500/20">
          <h2 className="text-2xl font-bold text-gray-100 mb-6">Performance by Course</h2>
          <ResponsiveContainer width="100%" height={400}>
            <BarChart data={filteredCourses.slice(0, 15)}>
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
              {filteredCourses.slice(0, 6).map((course, idx) => (
                <div key={idx} className="p-4 bg-black/60 rounded-lg border border-cyan-500/30">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="font-semibold text-gray-100">{course.location}</div>
                      <div className="text-sm text-gray-400">{course.discipline}</div>
                    </div>
                    <div className="text-right">
                      <div className="font-bold text-cyan-400">
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
        );
      })()}
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
