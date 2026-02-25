/**
 * API Service - Handles all HTTP requests to the FastAPI backend
 */

import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// ========== Athletes ==========

export const getAthletes = async (params?: {
  name?: string;
  country?: string;
  discipline?: string;
  tier?: string;
  limit?: number;
  offset?: number;
}) => {
  const response = await apiClient.get('/athletes', { params });
  return response.data;
};

export const getAthlete = async (fisCode: string) => {
  const response = await apiClient.get(`/athletes/${fisCode}`);
  return response.data;
};

export const getAthleteRaces = async (
  fisCode: string,
  params?: { discipline?: string; limit?: number; offset?: number }
) => {
  const response = await apiClient.get(`/athletes/${fisCode}/races`, { params });
  return response.data;
};

export const getAthleteMomentum = async (
  fisCode: string,
  params?: { discipline?: string; limit?: number }
) => {
  const response = await apiClient.get(`/athletes/${fisCode}/momentum`, { params });
  return response.data;
};

export const getAthleteCourses = async (
  fisCode: string,
  params?: { discipline?: string; min_races?: number }
) => {
  const response = await apiClient.get(`/athletes/${fisCode}/courses`, { params });
  return response.data;
};

export const getAthleteStrokesGained = async (
  fisCode: string,
  params?: { discipline?: string; limit?: number }
) => {
  const response = await apiClient.get(`/athletes/${fisCode}/strokes-gained`, { params });
  return response.data;
};

export const getAthleteStrokesGainedBib = async (
  fisCode: string,
  params?: { discipline?: string; limit?: number }
) => {
  const response = await apiClient.get(`/athletes/${fisCode}/strokes-gained-bib`, { params });
  return response.data;
};

export const getAthleteRegression = async (
  fisCode: string,
  params?: { discipline?: string; year?: number }
) => {
  const response = await apiClient.get(`/athletes/${fisCode}/regression`, { params });
  return response.data;
};

export const getAthleteCourseTraits = async (
  fisCode: string,
  params?: { discipline?: string; year?: number }
) => {
  const response = await apiClient.get(`/athletes/${fisCode}/course-traits`, { params });
  return response.data;
};

// ========== Races ==========

export const getRaces = async (params?: {
  discipline?: string;
  location?: string;
  country?: string;
  from_date?: string;
  to_date?: string;
  limit?: number;
  offset?: number;
}) => {
  const response = await apiClient.get('/races', { params });
  return response.data;
};

export const getRace = async (raceId: number) => {
  const response = await apiClient.get(`/races/${raceId}`);
  return response.data;
};

export const getRaceResults = async (
  raceId: number,
  params?: { limit?: number; offset?: number }
) => {
  const response = await apiClient.get(`/races/${raceId}/results`, { params });
  return response.data;
};

// ========== Leaderboards ==========

export const getLeaderboard = async (
  discipline: string,
  params?: { tier?: string; year?: number; limit?: number }
) => {
  const response = await apiClient.get(`/leaderboards/${discipline}`, { params });
  return response.data;
};

export const getHotStreak = async (params?: {
  discipline?: string;
  days?: number;
  limit?: number;
}) => {
  const response = await apiClient.get('/leaderboards/hot-streak', { params });
  return response.data;
};

// ========== Courses ==========

export const getCourses = async (params?: {
  discipline?: string;
  country?: string;
  location?: string;
  min_races?: number;
  limit?: number;
  offset?: number;
}) => {
  const response = await apiClient.get('/courses', { params });
  return response.data;
};

export const getCourseDifficulty = async (
  discipline: string,
  params?: { sort_by?: string; limit?: number }
) => {
  const response = await apiClient.get(`/courses/difficulty/${discipline}`, { params });
  return response.data;
};

// ========== Analytics ==========

export const getHomeAdvantage = async (params?: {
  discipline?: string;
  min_races?: number;
  limit?: number;
}) => {
  const response = await apiClient.get('/analytics/home-advantage', { params });
  return response.data;
};

// ========== Search ==========

export const globalSearch = async (query: string, params?: { type?: string; limit?: number }) => {
  const response = await apiClient.get('/search', { params: { q: query, ...params } });
  return response.data;
};

export default apiClient;
