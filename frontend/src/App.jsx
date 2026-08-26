import { Toaster } from 'react-hot-toast';
import { AuthProvider } from './auth/AuthProvider';
import DeactivationModal from './components/DeactivationModal';
import AppRoutes from './routes/AppRoutes';

function App() {
  return (
    <AuthProvider>
      <div className="app-container">
        <DeactivationModal />

        <Toaster
          position="top-right"
          toastOptions={{
            style: {
              background: '#fff',
              color: '#333',
              border: '1px solid #E0E0E0',
              padding: '16px',
              borderRadius: '8px',
              boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
            },
            success: {
              iconTheme: {
                primary: '#4CAF50',
                secondary: '#fff',
              },
              style: {
                borderLeft: '4px solid #4CAF50',
              },
            },
            error: {
              iconTheme: {
                primary: '#FFC107',
                secondary: '#fff',
              },
              style: {
                borderLeft: '4px solid #FFC107',
              },
            },
            loading: {
              iconTheme: {
                primary: '#FFC107',
                secondary: '#fff',
              },
            },
          }}
        />

        <AppRoutes />
      </div>
    </AuthProvider>
  );
}

export default App;
