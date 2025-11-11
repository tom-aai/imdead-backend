"""
Profile aggregation service.
Aggregates user profile data from all past interview call analyses.
"""

import json
import logging
import os
from anthropic import Anthropic
import sys

# Add parent directory to path to import utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_prompt

logger = logging.getLogger(__name__)


class ProfileAggregator:
	"""Service for aggregating user profile data from call analyses."""
	
	def __init__(self, supabase_client, anthropic_api_key: str = None):
		"""
		Initialize the profile aggregator.
		
		Args:
			supabase_client: Initialized Supabase client
			anthropic_api_key: Anthropic API key (optional, will use env var if not provided)
		"""
		self.supabase = supabase_client
		self.anthropic = Anthropic(api_key=anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"))
	
	def aggregate_user_profile(self, session_id: str) -> dict:
		"""
		Aggregate user profile data from all interview call analyses for the user.
		
		Reads the interview_call_analysis table for all records from the user who made the call.
		Extracts personality, myers-briggs score, and expertise description fields,
		combines them into one string per record, and returns the aggregated data.
		
		Args:
			session_id: The LiveKit session ID to identify the user
			
		Returns:
			dict: Processing result with status and aggregated data
			
		Raises:
			ValueError: If session_id is invalid or call not found
			Exception: If processing fails
		"""
		if not session_id:
			raise ValueError("session_id is required")
		
		if not self.supabase:
			raise Exception("Supabase client not initialized")
		
		logger.info("=" * 60)
		logger.info(f"👤 AGGREGATING USER PROFILE FOR SESSION: {session_id}")
		logger.info("=" * 60)
		
		# First, get the user_id from the session
		user_id = self._get_user_id_from_session(session_id)
		
		if not user_id:
			logger.error(f"❌ Could not find user_id for session: {session_id}")
			raise ValueError(f"User not found for session_id: {session_id}")
		
		logger.info(f"Found user_id: {user_id}")
		
		# Fetch all call analyses for this user
		response = self.supabase.table("interview_call_analysis")\
			.select("personality, myers_briggs_code, expertise_description, session_id, created_at")\
			.eq("user_id", user_id)\
			.order("created_at", desc=False)\
			.execute()
		
		if not response.data or len(response.data) == 0:
			logger.warning(f"⚠️ No call analyses found for user_id: {user_id}")
			return {
				"status": "success",
				"message": "No call analyses found for user",
				"session_id": session_id,
				"user_id": user_id,
				"record_count": 0,
				"aggregated_profiles": []
			}
		
		analyses = response.data
		logger.info(f"Found {len(analyses)} call analysis record(s) for user")
		
		# Aggregate profile data
		aggregated_profiles = []
		
		logger.info("\n" + "=" * 60)
		logger.info("📊 RAW PROFILE DATA FROM ALL SESSIONS")
		logger.info("=" * 60 + "\n")
		
		for idx, analysis in enumerate(analyses, 1):
			personality = analysis.get('personality', 'N/A')
			myers_briggs = analysis.get('myers_briggs_code', 'N/A')
			expertise = analysis.get('expertise_description', 'N/A')
			analysis_session = analysis.get('session_id', 'N/A')
			created_at = analysis.get('created_at', 'N/A')
			
			# Combine fields into one string
			combined_profile = (
				f"Session: {analysis_session}\n"
				f"Created: {created_at}\n"
				f"Myers-Briggs: {myers_briggs}\n"
				f"Personality: {personality}\n"
				f"Expertise: {expertise}"
			)
			
			aggregated_profiles.append({
				"session_id": analysis_session,
				"created_at": created_at,
				"myers_briggs_code": myers_briggs,
				"personality": personality,
				"expertise_description": expertise,
				"combined_profile": combined_profile
			})
			
			# Print raw data to terminal
			logger.info(f"Record {idx}/{len(analyses)}:")
			logger.info("-" * 60)
			logger.info(combined_profile)
			logger.info("-" * 60 + "\n")
		
		# Process with Claude to synthesize the profiles
		logger.info("Processing aggregated profiles with Claude...")
		synthesis = self._process_with_llm(user_id, aggregated_profiles)
		
		# Print synthesized results to terminal
		self._print_synthesis_results(synthesis)
		
		# Upload to user_profiles table
		uploaded_record = self._upload_to_supabase(user_id, synthesis, aggregated_profiles)
		
		logger.info("=" * 60)
		logger.info("✅ PROFILE AGGREGATION COMPLETE")
		logger.info(f"Processed {len(aggregated_profiles)} record(s)")
		logger.info(f"Profile uploaded to user_profiles with ID: {uploaded_record.get('id')}")
		logger.info("=" * 60)
		
		return {
			"status": "success",
			"message": "Profile data aggregated, synthesized, and uploaded successfully",
			"session_id": session_id,
			"user_id": user_id,
			"record_count": len(aggregated_profiles),
			"aggregated_profiles": aggregated_profiles,
			"synthesis": synthesis,
			"profile_id": uploaded_record.get('id')
		}
	
	def _process_with_llm(self, user_id: str, aggregated_profiles: list) -> dict:
		"""
		Process aggregated profile data with Claude to synthesize into a unified profile.
		
		Args:
			user_id: The user's ID
			aggregated_profiles: List of profile data from all sessions
			
		Returns:
			dict: Parsed JSON synthesis from Claude
		"""
		# Load prompt template from file
		prompt_template = load_prompt('profile_aggregation_prompt.txt')
		
		# Format the aggregated profiles for the prompt
		profiles_text = ""
		for idx, profile in enumerate(aggregated_profiles, 1):
			profiles_text += f"\n### Session {idx}\n"
			profiles_text += f"Session ID: {profile['session_id']}\n"
			profiles_text += f"Created: {profile['created_at']}\n"
			profiles_text += f"Myers-Briggs: {profile['myers_briggs_code']}\n"
			profiles_text += f"Personality: {profile['personality']}\n"
			profiles_text += f"Expertise: {profile['expertise_description']}\n"
		
		# Replace placeholders with actual data
		prompt = prompt_template.replace('{user_id}', user_id)\
			.replace('{session_count}', str(len(aggregated_profiles)))\
			.replace('{aggregated_profiles}', profiles_text)
		
		logger.info(f"Calling Claude API (synthesizing {len(aggregated_profiles)} session(s))")
		
		# Call Claude API
		message = self.anthropic.messages.create(
			model="claude-sonnet-4-20250514",
			max_tokens=8192,
			messages=[
				{
					"role": "user",
					"content": prompt
				}
			]
		)
		
		response_text = message.content[0].text
		
		# Parse JSON from response (handle potential markdown code blocks)
		if "```json" in response_text:
			# Extract JSON from markdown code block
			json_start = response_text.find("```json") + 7
			json_end = response_text.find("```", json_start)
			json_text = response_text[json_start:json_end].strip()
		elif "```" in response_text:
			# Extract from generic code block
			json_start = response_text.find("```") + 3
			json_end = response_text.find("```", json_start)
			json_text = response_text[json_start:json_end].strip()
		else:
			json_text = response_text.strip()
		
		return json.loads(json_text)
	
	def _print_synthesis_results(self, synthesis: dict):
		"""
		Print the synthesized profile results to the terminal in a formatted way.
		
		Args:
			synthesis: The synthesis result from Claude
		"""
		logger.info("\n" + "=" * 80)
		logger.info("🧠 SYNTHESIZED PROFILE ANALYSIS")
		logger.info("=" * 80 + "\n")
		
		logger.info(f"User ID: {synthesis.get('user_id')}")
		logger.info(f"Sessions Analyzed: {synthesis.get('sessions_analyzed')}")
		logger.info("")
		
		logger.info("=" * 80)
		logger.info("CONSOLIDATED MYERS-BRIGGS TYPE")
		logger.info("=" * 80)
		logger.info(f"{synthesis.get('consolidated_myers_briggs')}")
		logger.info("")
		logger.info("Reasoning:")
		logger.info(synthesis.get('reasoning'))
		logger.info("")
		
		logger.info("=" * 80)
		logger.info("COMPREHENSIVE PERSONALITY")
		logger.info("=" * 80)
		logger.info(synthesis.get('comprehensive_personality'))
		logger.info("")
		
		logger.info("=" * 80)
		logger.info("COMPREHENSIVE EXPERTISE")
		logger.info("=" * 80)
		logger.info(synthesis.get('comprehensive_expertise'))
		logger.info("")
	
	def _upload_to_supabase(self, user_id: str, synthesis: dict, aggregated_profiles: list) -> dict:
		"""
		Upload synthesized profile data to user_profiles table.
		
		Args:
			user_id: The user's ID
			synthesis: The synthesis result from Claude
			aggregated_profiles: List of profile data from all sessions (to get user names)
			
		Returns:
			dict: The inserted/updated record from Supabase
		"""
		# Get user's first and last name from the most recent analysis
		user_firstname = None
		user_lastname = None
		
		if aggregated_profiles and len(aggregated_profiles) > 0:
			# Try to get from aggregated profiles (need to fetch from full analysis record)
			latest_session_id = aggregated_profiles[-1].get('session_id')
			response = self.supabase.table("interview_call_analysis")\
				.select("user_firstname, user_lastname")\
				.eq("session_id", latest_session_id)\
				.limit(1)\
				.execute()
			
			if response.data and len(response.data) > 0:
				user_firstname = response.data[0].get('user_firstname')
				user_lastname = response.data[0].get('user_lastname')
		
		# Prepare data for upload (only include fields from Claude output)
		upload_data = {
			"user_id": user_id,  # The user_id is consistent across all tables
			"myers_briggs_code": synthesis.get("consolidated_myers_briggs"),
			"personality": synthesis.get("comprehensive_personality"),
			"expertise_description": synthesis.get("comprehensive_expertise")
		}
		
		# Add user names if we have them
		if user_firstname:
			upload_data["user_firstname"] = user_firstname
		if user_lastname:
			upload_data["user_lastname"] = user_lastname
		
		logger.info(f"Uploading synthesized profile to user_profiles table...")
		logger.info(f"  User ID: {user_id}")
		logger.info(f"  Myers-Briggs: {synthesis.get('consolidated_myers_briggs')}")
		
		# Upsert to user_profiles table (insert or update if user_id exists)
		# First check if a profile exists for this user
		existing = self.supabase.table("user_profiles")\
			.select("id")\
			.eq("user_id", user_id)\
			.execute()
		
		if existing.data and len(existing.data) > 0:
			# Update existing record
			profile_id = existing.data[0].get('id')
			logger.info(f"  Updating existing profile (ID: {profile_id})")
			response = self.supabase.table("user_profiles")\
				.update(upload_data)\
				.eq("user_id", user_id)\
				.execute()
		else:
			# Insert new record
			logger.info(f"  Creating new profile")
			response = self.supabase.table("user_profiles")\
				.insert(upload_data)\
				.execute()
		
		if response.data:
			logger.info(f"✅ Successfully uploaded profile for user: {user_id}")
			return response.data[0]
		else:
			raise Exception("Failed to upload profile to Supabase")
	
	def _get_user_id_from_session(self, session_id: str) -> str:
		"""
		Get the user_id associated with a session.
		
		Args:
			session_id: The LiveKit session ID
			
		Returns:
			str: The user_id, or None if not found
		"""
		# Try interview_call_analysis first (most recent table)
		response = self.supabase.table("interview_call_analysis")\
			.select("user_id")\
			.eq("session_id", session_id)\
			.limit(1)\
			.execute()
		
		if response.data and len(response.data) > 0:
			return response.data[0].get('user_id')
		
		# Fallback to interview_calls table
		response = self.supabase.table("interview_calls")\
			.select("user_id")\
			.eq("session_id", session_id)\
			.limit(1)\
			.execute()
		
		if response.data and len(response.data) > 0:
			return response.data[0].get('user_id')
		
		return None

