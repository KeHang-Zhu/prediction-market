import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

// No StrictMode: this is a display-only app whose store drives a setTimeout-based
// replay loop, and StrictMode's dev double-mount would start the loop twice.
createRoot(document.getElementById('root')!).render(<App />)
