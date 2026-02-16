import { Link } from 'react-router-dom';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import { useState } from 'react';
import SearchModal from './SearchModal';

export default function Header() {
  const [isSearchOpen, setIsSearchOpen] = useState(false);

  return (
    <>
      <header className="bg-gray-900 shadow-xl border-b border-gray-800">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            {/* Logo */}
            <Link to="/" className="flex items-center space-x-3 group">
              <div className="text-2xl font-bold text-primary-400 group-hover:text-primary-300 transition-colors">⛷️</div>
              <div>
                <h1 className="text-xl font-bold text-gray-100 group-hover:text-primary-400 transition-colors">
                  Alpine Analytics Pro
                </h1>
                <p className="text-xs text-gray-500">Professional Athlete Intelligence</p>
              </div>
            </Link>

            {/* Search */}
            <button
              onClick={() => setIsSearchOpen(true)}
              className="flex items-center space-x-2 px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg hover:bg-gray-700 hover:border-primary-500/50 transition-all"
            >
              <MagnifyingGlassIcon className="h-5 w-5 text-gray-400" />
              <span className="text-gray-400">Search athletes...</span>
              <kbd className="hidden sm:inline-flex items-center px-2 py-0.5 border border-gray-700 rounded text-xs text-gray-500">
                ⌘K
              </kbd>
            </button>
          </div>
        </div>
      </header>

      <SearchModal isOpen={isSearchOpen} onClose={() => setIsSearchOpen(false)} />
    </>
  );
}
