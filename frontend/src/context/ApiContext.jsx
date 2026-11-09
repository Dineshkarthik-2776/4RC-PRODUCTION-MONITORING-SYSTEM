import React, { createContext, useState, useContext, useEffect, useRef } from 'react';
import axios from 'axios';
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;


// Create Context
const ApiContext = createContext();

// Custom hook to use the context
export const useApiData = () => {
  const context = useContext(ApiContext);
  if (!context) {
    throw new Error('useApiData must be used within ApiProvider');
  }
  return context;
};

// Provider Component
export const ApiProvider = ({ children }) => {
  const getLocalDateString = () => {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, '0');
    const day = String(now.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };

  const [changeoverData, setChangeoverData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [dateRange, setDateRange] = useState({
    from_date: getLocalDateString(),
    to_date: getLocalDateString()
  });
  const [selectedShift, setSelectedShift] = useState(''); // '', 'A', 'B', 'C'
  const [selectedRecipe, setSelectedRecipe] = useState(''); // For recipe filter
  const [recipeNames, setRecipeNames] = useState([]); // List of all recipe names
  const [overshootOptions, setOvershootOptions] = useState([]); // Dynamic overshoot categories/reasons
  
  const pollingIntervalRef = useRef(null);

  // Format date to YYYY-MM-DD
  const formatDate = (date) => {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, '0');
    const day = String(date.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };

  // Fetch data from API
  const fetchChangeoverStats = async (fromDate, toDate, shift = '') => {
    setLoading(true);
    setError(null);

    try {
      const token = localStorage.getItem('authToken');
      
      if (!token) {
        throw new Error('No authentication token found');
      }

      const params = {
        from_date: fromDate,
        to_date: toDate
      };
      
      if (shift) {
        params.shift = shift;
      }

      const response = await axios.get(`${API_BASE_URL}/api/changeover-stats/`, {
        params,
        headers: {
          'Authorization': `Token ${token}`
        }
      });

      setChangeoverData(response.data);
      console.log('Changeover stats fetched:', response.data);
      return response.data;
    } catch (err) {
      const errorMessage = err.response?.data?.message || err.message || 'Failed to fetch data';
      setError(errorMessage);
      console.error('API Error:', err);
      return null;
    } finally {
      setLoading(false);
    }
  };

  // Apply the whole filter bar at once.
  //
  // The filters used to take effect one at a time: picking a shift
  // refetched immediately, then picking a recipe refetched again, so
  // narrowing from "all shifts, all recipes, this week" to one
  // combination walked the dashboard through intermediate results
  // nobody asked to see. Everything is now staged in the toolbar and
  // committed here in a single pass.
  const applyFilters = ({ startDate, endDate, shift, recipe }) => {
    const formattedStart = formatDate(startDate);
    const formattedEnd = formatDate(endDate);

    setDateRange({ from_date: formattedStart, to_date: formattedEnd });
    setSelectedShift(shift);
    setSelectedRecipe(recipe);

    fetchChangeoverStats(formattedStart, formattedEnd, shift);
  };

  // Fetch recipe names from API
  const fetchRecipeNames = async () => {
    try {
      const token = localStorage.getItem('authToken');
      if (!token) return;

      const response = await axios.get(`${API_BASE_URL}/api/recipe-export-master/`, {
        params: { format: 'json' },
        headers: { 'Authorization': `Token ${token}` }
      });

      const names = Object.keys(response.data);
      setRecipeNames(names);
      localStorage.setItem('recipeNames', JSON.stringify(names));
      console.log('Recipe names fetched:', names);
    } catch (err) {
      console.error('Failed to fetch recipe names:', err);
    }
  };

  // Fetch overshoot options from API
  const fetchOvershootOptions = async () => {
    try {
      const token = localStorage.getItem('authToken');
      if (!token) return;

      const response = await axios.get(`${API_BASE_URL}/api/overshoot-options/`, {
        headers: { 'Authorization': `Token ${token}` }
      });

      setOvershootOptions(response.data);
      console.log('Overshoot options fetched:', response.data);
    } catch (err) {
      console.error('Failed to fetch overshoot options:', err);
    }
  };

  // Start polling every 5 minutes
  const startPolling = () => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
    }

    pollingIntervalRef.current = setInterval(() => {
      console.log('Polling API (every 5 minutes)...');
      fetchChangeoverStats(dateRange.from_date, dateRange.to_date, selectedShift);
    }, 5 * 60 * 1000); // 5 minutes in milliseconds
  };

  // Stop polling
  const stopPolling = () => {
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
  };

  // Initial fetch and start polling on mount
  useEffect(() => {
    fetchChangeoverStats(dateRange.from_date, dateRange.to_date, selectedShift);
    fetchRecipeNames(); // Fetch recipe names on mount
    fetchOvershootOptions(); // Fetch overshoot options on mount
    startPolling();

    // Cleanup on unmount
    return () => {
      stopPolling();
    };
  }, []); // Only run once on mount

  // Restart polling when date range changes
  useEffect(() => {
    if (pollingIntervalRef.current) {
      stopPolling();
      startPolling();
    }
  }, [dateRange]);

  const value = {
    changeoverData,
    loading,
    error,
    dateRange,
    selectedShift,
    selectedRecipe,
    recipeNames,
    overshootOptions,
    applyFilters,
    fetchRecipeNames,
    fetchOvershootOptions,
    refreshData: () => fetchChangeoverStats(dateRange.from_date, dateRange.to_date, selectedShift),
    API_BASE_URL,
  };

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
};
