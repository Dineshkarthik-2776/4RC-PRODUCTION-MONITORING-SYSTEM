import React from 'react';
import './App.css';
import Header from './component/Header';
import Charts from './component/Charts';
import ToggleBtn from './component/ToggleBtn';
import SetupDetails from './component/SetupDetails';
import DateIP from './component/DateIP';
import Login from './component/Login';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { ApiProvider } from './context/ApiContext';
import { NotificationProvider } from './context/NotificationContext';

// Protected Route Component
const ProtectedRoute = ({ children }) => {
  const token = localStorage.getItem('authToken');
  
  if (!token) {
    return <Navigate to="/login" replace />;
  }
  
  return children;
};

// Dashboard Layout (Header + DateIP + Content)
function DashboardLayout({ children }) {
  return (
    <ApiProvider>
      <Header />
      <DateIP />
      <ToggleBtn />
      {children}
    </ApiProvider>
  );
}

// Main Layout Component
function AppLayout() {
  const location = useLocation();

  return (
    <div className='app'>
      <Routes>
        {/* Public Route - Login */}
        <Route path="/login" element={<Login />} />

        {/* Protected Routes - Dashboard */}
        <Route 
          path="/" 
          element={
            <ProtectedRoute>
              <DashboardLayout>
               
              </DashboardLayout>
            </ProtectedRoute>
          } 
        />
        
        <Route 
          path="/setup" 
          element={
            <ProtectedRoute>
              <DashboardLayout>
                <SetupDetails />
              </DashboardLayout>
            </ProtectedRoute>
          } 
        />

        {/* Redirect unknown routes to login */}
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    </div>
  );
}

function App() {
  return (
    <NotificationProvider>
      <AppLayout />
    </NotificationProvider>
  );
}

export default App;
