import { Routes, Route, Navigate } from 'react-router-dom';
import ProtectedRoute from './ProtectedRoute';
import Login from '../pages/Login';
import DashboardOverview from '../pages/DashboardOverview';
import DataVerification from '../pages/DataVerification';
import Duplicates from '../pages/Duplicates';
import Comparison from '../pages/Comparison';
import MissingData from '../pages/MissingData';
import Settings from '../pages/Settings';

function withLayout(element) {
  return <ProtectedRoute>{element}</ProtectedRoute>;
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/login" element={<Login />} />
      <Route path="/dashboard" element={withLayout(<DashboardOverview />)} />
      <Route path="/verification" element={withLayout(<DataVerification />)} />
      <Route path="/duplicates" element={withLayout(<Duplicates />)} />
      <Route path="/comparison" element={withLayout(<Comparison />)} />
      <Route path="/missing" element={withLayout(<MissingData />)} />
      <Route path="/settings" element={withLayout(<Settings />)} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
